import os
import re
import json
import time
from zipfile import ZipFile
from io import BytesIO
from urllib.parse import urljoin

try:
	from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
	Image = None  # type: ignore

import requests  # type: ignore
from bs4 import BeautifulSoup  # type: ignore

DEBUG = True
TIMEOUT = 20
MIN_IMAGE_BYTES = 12_000
HEADERS = {
	"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
	"Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}

_session = requests.Session()
_session.headers.update(HEADERS)


def _log(*args):
	if DEBUG:
		print(*args, flush=True)


def _sanitize_filename(name: str, fallback: str = "ad") -> str:
	if not name:
		name = fallback
	name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()
	return name[:200] or fallback


def _guess_ext(data: bytes) -> str:
	if not data or len(data) < 4:
		return ".jpg"
	sig = data[:4]
	if sig.startswith(b"\xff\xd8"):
		return ".jpg"
	if sig.startswith(b"\x89PNG"):
		return ".png"
	if sig[:3] == b"GIF":
		return ".gif"
	return ".jpg"


def _strip_metadata(img_bytes: bytes) -> bytes:
	if Image is None:
		return img_bytes
	try:
		im = Image.open(BytesIO(img_bytes))
		fmt = (im.format or "JPEG").upper()
		out = BytesIO()
		if fmt in ("JPEG", "JPG"):
			if im.mode in ("RGBA", "P"):
				im = im.convert("RGB")
			im.save(out, format="JPEG", quality=95, optimize=True)
		elif fmt == "PNG":
			clean = Image.new(im.mode, im.size)
			clean.putdata(list(im.getdata()))
			clean.save(out, format="PNG", optimize=True)
		else:
			im.save(out, format=fmt)
		return out.getvalue()
	except Exception:
		return img_bytes


def _ensure_abs(base: str, u: str | None) -> str | None:
	if not u:
		return None
	if u.startswith("//"):
		u = "https:" + u
	if u.startswith("/"):
		u = urljoin(base, u)
	return u


def _http_get(url: str):
	if url.startswith("//"):
		url = "https:" + url
	r = _session.get(url, timeout=TIMEOUT)
	r.raise_for_status()
	return r


def _download(url: str) -> bytes | None:
	try:
		r = _session.get(url, timeout=(10, 20))
		r.raise_for_status()
		return r.content
	except Exception:
		return None


def _parse_price_string(s: str) -> str:
	s = s.strip()
	m = re.search(r'^\s*(\d[\d\.\,\s]*)\s*(€|EUR)?\s*$', s, re.IGNORECASE)
	if m:
		num = m.group(1).strip()
		cur = m.group(2)
		return f"{num} {('EUR' if not cur else cur.upper().replace('€','EUR'))}".strip()
	m = re.search(r'(\d[\d\.\,\s]*)\s*(€|EUR)', s, re.IGNORECASE)
	if m:
		return f"{m.group(1).strip()} {m.group(2).upper().replace('€','EUR')}"
	return s


# ---- minimal parsers (kleinanzeigen / ebay / willhaben) ----

def _kleinanzeigen_categories(soup: BeautifulSoup, html: str):
	cats = []
	for sc in soup.find_all("script", attrs={"type": "application/ld+json"}):
		raw = sc.string or sc.text
		if not raw:
			continue
		try:
			data = json.loads(raw)
		except Exception:
			continue
		def pull(obj):
			out = []
			if isinstance(obj, dict):
				t = (obj.get("@type") or obj.get("type") or "").lower()
				if t == "breadcrumblist" and "itemListElement" in obj:
					items = obj["itemListElement"]
					if isinstance(items, list):
						for it in items:
							if isinstance(it, dict):
								name = it.get("name") or ""
								if not name:
									item = it.get("item")
									if isinstance(item, dict):
										name = item.get("name", "")
								if name:
									out.append(str(name).strip())
				for v in obj.values():
					out.extend(pull(v))
			elif isinstance(obj, list):
				for it in obj:
					out.extend(pull(it))
			return out
		names = [n for n in pull(data) if n]
		if names:
			cats = names
			break
	if not cats:
		crumbs = soup.select('nav[aria-label*="Bread"] a, nav[aria-label*="Brot"] a, .breadcrumb a, .breadcrumbs a, ol[itemtype*="BreadcrumbList"] a, ul[itemtype*="BreadcrumbList"] a')
		names = [a.get_text(" ", strip=True) for a in crumbs if a.get_text(strip=True)]
		if names:
			cats = names
	cleaned = []
	for n in cats:
		t = n.strip()
		if not t:
			continue
		if t.lower().startswith("kleinanzeigen"):
			continue
		if t not in cleaned:
			cleaned.append(t)
	human = " > ".join(cleaned) if cleaned else ""
	return cleaned, human


def _parse_kleinanzeigen(url: str, soup: BeautifulSoup, html: str):
	title = ""
	h1 = soup.find("h1")
	if h1:
		title = h1.get_text(" ", strip=True)
	description = ""
	el_desc = soup.select_one('[data-testid="ad-description"], [data-testid*="description"], [itemprop="description"], .aditem-main--description, #addescription, p.text-module__text')
	if el_desc:
		description = el_desc.get_text("\n", strip=True)
	if not description:
		md = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
		if md and md.get("content"):
			description = md["content"].strip()
	price = ""
	m_amount = soup.find("meta", itemprop="price")
	m_curr = soup.find("meta", itemprop="priceCurrency")
	if m_amount and m_amount.get("content"):
		amount = m_amount["content"].strip()
		curr = (m_curr.get("content").strip() if m_curr and m_curr.get("content") else "EUR")
		price = _parse_price_string(f"{amount} {curr}")
	if not price:
		for sel in ('[data-testid="price"]','[data-testid*="Price"]','.price-block__price','.main-price','[itemprop="price"]','[class*="price"]'):
			elp = soup.select_one(sel)
			if elp and elp.get_text(strip=True):
				price = _parse_price_string(elp.get_text(" ", strip=True))
				break
	img_urls = []
	gallery = soup.select_one('[data-testid*="gallery"], [class*="gallery"], [role="region"][aria-label*="Bilder"], .image-gallery')
	if gallery:
		for img in gallery.find_all("img"):
			src = img.get("data-src") or img.get("src") or img.get("data-zoom") or img.get("data-original")
			if not src and (img.get("srcset") or img.get("data-srcset")):
				raw = img.get("srcset") or img.get("data-srcset")
				src = raw.split(",")[-1].split()[0]
			if src:
				if src.startswith("//"):
					src = "https:" + src
				if src.startswith("/"):
					src = urljoin(url, src)
				if src.startswith("http"):
					img_urls.append(src)
	return (title or "No Title").strip(), (price or "No Price").strip(), (description or "No Description").strip(), img_urls


def _parse_ebay(url: str, soup: BeautifulSoup, html: str):
	title = ""
	t1 = soup.find(id="itemTitle")
	if t1:
		title = t1.get_text(" ", strip=True).replace("Details about  ", "")
	if not title:
		mt = soup.find("meta", property="og:title")
		if mt and mt.get("content"):
			title = mt.get("content").strip()
	price = ""
	xp = soup.select_one(".x-price-primary, .x-bin-price, .x-offer-price, [itemprop='price']")
	if xp and xp.get_text(strip=True):
		price = _parse_price_string(xp.get_text(" ", strip=True))
	if not price:
		for sel in ("#prcIsum", "#prcIsum_bidPrice", "#mm-saleDscPrc"):
			el = soup.select_one(sel)
			if el and el.get_text(strip=True):
				price = _parse_price_string(el.get_text(" ", strip=True))
				break
	if not price:
		m_amount = soup.find("meta", itemprop="price")
		m_curr = soup.find("meta", itemprop="priceCurrency")
		if m_amount and m_amount.get("content"):
			amount = m_amount["content"].strip()
			curr = (m_curr.get("content").strip() if m_curr and m_curr.get("content") else "EUR")
			price = _parse_price_string(f"{amount} {curr}")
	description = ""
	md = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
	if md and md.get("content"):
		description = md["content"].strip()
	img_urls = []
	for img in soup.select('div.ux-image-carousel img, div.ux-image-carousel-item img, ul#vertical-align-gallery img, #vi_main_img_fs img, #vi_main_img_fs_slider img, div#vi_gallery img'):
		cand = img.get("data-zoom-src") or img.get("data-zoom") or img.get("data-src") or img.get("src")
		if not cand:
			srcset = img.get("srcset") or img.get("data-srcset")
			if srcset:
				parts = [p.strip() for p in srcset.split(",") if p.strip()]
				if parts:
					cand = parts[-1].split()[0]
		if not cand:
			continue
		cand = _ensure_abs(url, cand)
		if cand and "i.ebayimg.com" in cand:
			# try to get large image
			cand = re.sub(r's-l\d+(\.\w{3,4})(\?|$)', r's-l1600\1\2', cand)
			img_urls.append(cand)
	return (title or "No Title").strip(), (price or "No Price").strip(), (description or "No Description").strip(), img_urls


def _parse_willhaben(url: str, soup: BeautifulSoup, html: str):
	title = ""
	h1 = soup.find("h1")
	if h1:
		title = h1.get_text(" ", strip=True)
	price = ""
	for sel in ('[data-testid="ad-detail-price"]','.price-value', '.price', '.ad-price', '.price-block', '.priceBox','[itemprop="price"]'):
		el = soup.select_one(sel)
		if el and el.get_text(strip=True):
			price = _parse_price_string(el.get_text(" ", strip=True))
			break
	if not price:
		md = soup.find("meta", property="product:price:amount")
		mc = soup.find("meta", property="product:price:currency")
		if md and md.get("content"):
			price = _parse_price_string(f"{md['content']} {(mc.get('content') if mc else '')}")
	description = ""
	el_primary = soup.select_one('[data-testid="ad-detail-description"]')
	if el_primary and el_primary.get_text(strip=True):
		description = el_primary.get_text("\n", strip=True)
	img_urls = []
	for img in soup.find_all("img"):
		cand = img.get("data-src") or img.get("src")
		if not cand:
			ss = img.get("srcset") or img.get("data-srcset")
			if ss:
				cand = ss.split(",")[-1].split()[0]
		if not cand:
			continue
		u2 = _ensure_abs(url, cand)
		if u2:
			img_urls.append(u2)
	return (title or "No Title").strip(), (price or "No Price").strip(), (description or "No Description").strip(), img_urls


def save_ad(url: str) -> str | None:
	print(f"\nОбрабатываю: {url}", flush=True)
	try:
		r = _http_get(url)
	except Exception as e:
		print(f"  не открыл страницу: {e}", flush=True)
		return None

	html = r.text
	soup = BeautifulSoup(html, "html.parser")
	lo = url.lower()

	if "kleinanzeigen.de" in lo:
		title, price, description, img_urls = _parse_kleinanzeigen(url, soup, html)
	elif "ebay." in lo:
		title, price, description, img_urls = _parse_ebay(url, soup, html)
	elif "willhaben.at" in lo:
		title, price, description, img_urls = _parse_willhaben(url, soup, html)
	else:
		# generic fallbacks
		title = (soup.find("title").get_text(" ", strip=True) if soup.find("title") else "No Title")
		md = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
		description = (md.get("content").strip() if md and md.get("content") else "No Description")
		price = "No Price"
		img_urls = [im.get("src") for im in soup.find_all("img") if im.get("src")] or []

	# Ensure archive directory
	archive_dir = os.getenv("ARCHIVE_DIR") or os.getcwd()
	os.makedirs(archive_dir, exist_ok=True)

	# Referer for image fetch
	_session.headers["Referer"] = url

	images: list[tuple[str, bytes]] = []
	seen: set[str] = set()
	print(f"  картинок-кандидатов: {len(img_urls)}", flush=True)
	for u in img_urls:
		if not u or u in seen:
			continue
		seen.add(u)
		print(f"+ сохраню {u}", flush=True)
		data = _download(u)
		if not data or len(data) < MIN_IMAGE_BYTES:
			continue
		data = _strip_metadata(data)
		fname = f"{len(images)+1}{_guess_ext(data)}"
		images.append((fname, data))

	if not images:
		og = soup.find("meta", property="og:image")
		if og and og.get("content"):
			u = _ensure_abs(url, og.get("content"))
			if u:
				print(f"+ сохраню (og:image) {u}", flush=True)
				data = _download(u)
				if data and len(data) >= MIN_IMAGE_BYTES:
					data = _strip_metadata(data)
					images.append(("1" + _guess_ext(data), data))

	safe_title = _sanitize_filename(title or f"ad_{int(time.time())}")
	archive = safe_title + ".zip"
	# avoid collisions
	i = 1
	while os.path.exists(os.path.join(archive_dir, archive)):
		archive = f"{safe_title}_{i}.zip"
		i += 1

	info = (
		f"Title: {title}\n"
		f"Price: {price}\n"
		f"URL: {url}\n"
		f"Saved at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
		f"Description:\n{description}\n"
	)

	try:
		full_path = os.path.abspath(os.path.join(archive_dir, archive))
		with ZipFile(full_path, "w") as z:
			z.writestr("info.txt", info)
			for name, data in images:
				z.writestr(name, data)
		print(f"OK: {full_path} — фото: {len(images)}; текст сохранён.", flush=True)
		return full_path
	except Exception as e:
		print(f"  ошибка записи архива: {e}", flush=True)
		return None
