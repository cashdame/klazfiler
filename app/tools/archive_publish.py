# app/tools/archive_publish.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import time
import secrets
import logging
import random
import difflib
from typing import Dict, List, Optional, Tuple
from zipfile import ZipFile

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.config import settings
from app.services.accounts import list_accounts as sp_list_accounts

# SuitePro API фасад
from app.services.add import (
    list_categories as sp_list_categories,              # GET /categories
    get_category_metadata as sp_get_category_metadata,  # GET /metadata?id=...
    upload_images as sp_upload_images,                  # POST /classifieds/images
    publish_ad as sp_publish_ad,                        # POST /classifieds/
)

log = logging.getLogger("klazfiler.archive.publish")

# ====== временное хранилище токенов callback ======
_TOKEN_TTL = 15 * 60
_STORE: Dict[str, Dict] = {}

def _put(data: Dict) -> str:
    tok = secrets.token_urlsafe(12)
    data["_ts"] = time.time()
    _STORE[tok] = data
    return tok

def _get(tok: str) -> Optional[Dict]:
    d = _STORE.get(tok)
    if not d:
        return None
    if time.time() - d.get("_ts", 0) > _TOKEN_TTL:
        _STORE.pop(tok, None)
        return None
    return d

def _cleanup():
    now = time.time()
    for k, v in list(_STORE.items()):
        if now - v.get("_ts", 0) > _TOKEN_TTL:
            _STORE.pop(k, None)

# ====== маленький телеграм-логер ======
class TgStatus:
    def __init__(self, call: CallbackQuery):
        self.call = call
        self.msg = None
        self.lines: List[str] = []

    async def start(self, head: str):
        self.lines = [f"🧰 {head}"]
        self.msg = await self.call.message.answer("\n".join(self.lines))
        return self

    async def add(self, text: str, ok: Optional[bool] = None, icon: Optional[str] = None):
        if icon is None:
            icon = "✅" if ok is True else ("❌" if ok is False else "📝")
        self.lines.append(f"{icon} {text}")
        try:
            await self.msg.edit_text("\n".join(self.lines))
        except Exception:
            self.msg = await self.call.message.answer("\n".join(self.lines))

    async def code(self, title: str, body, max_len: int = 1800):
        if not isinstance(body, str):
            try:
                body = json.dumps(body, ensure_ascii=False, indent=2)
            except Exception:
                body = str(body)
        body = body[:max_len]
        self.lines.append(f"🗂 {title}:\n<code>{body}</code>")
        try:
            await self.msg.edit_text("\n".join(self.lines), parse_mode="HTML")
        except Exception:
            self.msg = await self.call.message.answer("\n".join(self.lines), parse_mode="HTML")


# ====== утилиты чтения архива ======
def _read_info(zip_path: str) -> Tuple[str, str, str, str, str]:
    title = price = url = saved = desc = ""
    try:
        with ZipFile(zip_path, "r") as z:
            name = None
            for zi in z.infolist():
                if not zi.is_dir() and zi.filename.lower().endswith("info.txt"):
                    name = zi.filename
                    break
            if not name:
                return title, price, url, saved, desc
            text = z.read(name).decode("utf-8", errors="ignore")
            m = re.search(r"^Title:\s*(.+)$", text, re.M)
            title = (m.group(1).strip() if m else "")
            m = re.search(r"^Price:\s*(.+)$", text, re.M)
            price = (m.group(1).strip() if m else "")
            m = re.search(r"^URL:\s*(.+)$", text, re.M)
            url = (m.group(1).strip() if m else "")
            m = re.search(r"^Saved at:\s*(.+)$", text, re.M)
            saved = (m.group(1).strip() if m else "")
            m = re.search(r"^Description:\s*\n([\s\S]+)$", text, re.M)
            desc = (m.group(1).strip() if m else "")
    except Exception as e:
        log.warning("read info.txt failed: %s", e)
    return title, price, url, saved, desc

def _extract_images(zip_path: str, max_images: int = 10) -> List[Tuple[str, bytes]]:
    exts = (".jpg", ".jpeg", ".png", ".gif", ".webp")
    out: List[Tuple[str, bytes]] = []
    try:
        with ZipFile(zip_path, "r") as z:
            idx = 1
            for zi in z.infolist():
                if zi.is_dir():
                    continue
                low = zi.filename.lower()
                if not any(low.endswith(e) for e in exts):
                    continue
                try:
                    data = z.read(zi)
                    if data and len(data) >= 10_000:
                        fname = zi.filename.rsplit("/", 1)[-1] or f"image_{idx}.jpg"
                        if not any(fname.lower().endswith(e) for e in exts):
                            fname = f"image_{idx}.jpg"
                        out.append((fname, data))
                        idx += 1
                        if len(out) >= max_images:
                            break
                except Exception:
                    continue
    except Exception as e:
        log.warning("extract images failed: %s", e)
    return out

_PRICE_NUM_RE = re.compile(r"(\d[\d\.\s,]*)")
def _price_from_text(price_str: str) -> Tuple[str, Optional[int]]:
    s = (price_str or "").strip()
    m = _PRICE_NUM_RE.search(s)
    if not m:
        return "PLEASE_CONTACT", None
    raw = m.group(1).replace(".", "").replace(" ", "").replace(",", ".")
    try:
        val = float(raw)
        amt = int(round(val))
        return "SPECIFIED_AMOUNT", max(1, amt)
    except Exception:
        return "PLEASE_CONTACT", None

# набор редких индексов
_SMALL_PLZ = [
    "88131", "78176", "25746", "31812", "99510", "37308", "39615", "54634", "15926",
    "16845", "24837", "25813", "25980", "87727", "83115", "94315", "06721"
]

def _random_postcode() -> str:
    return random.choice(_SMALL_PLZ)

# ====== OpenAI ======

def _pretty(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)

def _oa_chat_json(messages: List[Dict]) -> Dict:
    api_key = (settings.OPENAI_API_KEY or "").strip()
    if not api_key:
        return {}
    base = settings.OPENAI_API_BASE.rstrip("/")
    model = settings.OPENAI_MODEL

    import requests, logging, json as _json
    log_oa = logging.getLogger("klazfiler.openai")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if getattr(settings, "OPENAI_PROJECT", ""):
        headers["OpenAI-Project"] = settings.OPENAI_PROJECT
    if getattr(settings, "OPENAI_ORG", ""):
        headers["OpenAI-Organization"] = settings.OPENAI_ORG

    body = {"model": model, "response_format": {"type": "json_object"}, "messages": messages}

    # лог запроса (отрежем длинное)
    try:
        log_oa.info("[OpenAI-REQ]\n%s", _pretty(body)[:4000])
    except Exception:
        pass

    try:
        r = requests.post(f"{base}/v1/chat/completions", headers=headers, json=body, timeout=settings.API_TIMEOUT)
        txt = r.text
        try:
            data = r.json()
        except Exception:
            data = {}

        log_oa.info("[OpenAI-RESP] status=%s", r.status_code)
        # лог тела ответа
        log_oa.info("[OpenAI-BODY]\n%s", (txt[:4000] if isinstance(txt, str) else _pretty(data)[:4000]))

        r.raise_for_status()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        log_oa.warning("openai failed: %s", e)
        return {}

def _normalize_choice(choice, options):
    if not choice or not options:
        return None
    # гарантируем строки
    opts = [str(o) for o in options]
    c = str(choice).strip().lower().replace(" ", "_").replace("-", "_")
    for o in opts:
        if c == o.lower():
            return o
    import difflib
    best = difflib.get_close_matches(c, [o.lower() for o in opts], n=1, cutoff=0.6)
    if best:
        idx = [o.lower() for o in opts].index(best[0])
        return opts[idx]
    return None

def _choose_art_via_gpt(options: list[str], title: str, description: str) -> str | None:
    if not options:
        return None
    # просим ТОЛЬКО одно из options
    sys = (
        "Du hilfst, eine passende Kategorie-Option zu wählen. "
        "Gib die Antwort ausschließlich als JSON-Objekt: {\"art\": \"<wert>\"}. "
        "Der Wert muss GENAU einer der vorgeschlagenen Optionen sein."
    )
    user = {
        "title": title,
        "description": description,
        "options": options
    }
    resp = _oa_chat_json([
        {"role": "system", "content": sys},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)}
    ]) or {}

    # ожидаем {"art": "<one-of-options>"}
    choice = None
    if isinstance(resp, dict):
        # некоторые модели заворачивают ответ внутрь ключей; поддержим оба варианта
        if "art" in resp and isinstance(resp["art"], str):
            choice = resp["art"]
        elif "ad" in resp and isinstance(resp["ad"], dict) and isinstance(resp["ad"].get("art"), str):
            choice = resp["ad"]["art"]

    return _normalize_choice(choice, options)

def _extract_attribute_schema(meta_payload: dict) -> list[dict]:
    schema = []
    def walk(node):
        if isinstance(node, dict):
            if "attributes" in node and isinstance(node["attributes"], dict):
                attrs = node["attributes"].get("attribute")
                if isinstance(attrs, list):
                    for a in attrs:
                        if not isinstance(a, dict): 
                            continue
                        name = a.get("name")
                        sv = a.get("supported-value") or []
                        opts = [o.get("value") for o in sv if isinstance(o, dict) and o.get("value")]
                        schema.append({"name": name, "options": opts})
                    return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(meta_payload)
    return schema

def _first_attr(schema: list[dict], suffix: str) -> dict | None:
    for a in schema:
        n = a.get("name")
        if isinstance(n, str) and n.endswith("." + suffix):
            return a
    return None


def _gpt_fill_ad(info: Dict, leaves: List[Dict]) -> Dict:
    """
    Просим вернуть JSON:
    {
      title, description,
      priceType: 'SPECIFIED_AMOUNT'|'PLEASE_CONTACT',
      amount: int|null,
      attributes: object,
      category_id: string (из списка leaves),
      postcode: string (небольшой город Германии: 88131/78176/25980 и т.п.)
    }
    """
    if not (settings.OPENAI_API_KEY or "").strip():
        return {}
    cats_compact = [{"id": c["id"], "name": c["name"], "path": c["path"]} for c in leaves][:1000]
    sys = (
        "Ты помощник по заполнению объявлений. Ответ только JSON. "
        "category_id должен быть из переданного списка категорий. "
        "postcode выбери для небольшого города Германии, можно из примеров: 88131, 78176, 25980."
    )
    user = json.dumps({"ad": info, "categories": cats_compact}, ensure_ascii=False)
    return _oa_chat_json([
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]) or {}

# ====== categories helpers ======
async def _fetch_categories_tree() -> List[Dict]:
    try:
        ok, data, status = await sp_list_categories()
    except Exception as e:
        log.info("categories fetch exception: %s", e)
        return []

    # простой список
    if ok and isinstance(data, list):
        return data

    # ebay-schema
    if isinstance(data, dict):
        ns_key = next((k for k in data.keys() if k.endswith("}categories")), None)
        if ns_key:
            cat_root = data.get(ns_key) or {}
            val = cat_root.get("value") if isinstance(cat_root, dict) else None
            if isinstance(val, dict):
                cats = val.get("category")
                if isinstance(cats, list):
                    return _unpack_ebay_categories(cats)

    log.info("categories fail: %s %s", ok if ok is not None else status, str(data)[:400])
    return []

def _unpack_ebay_categories(nodes: List[Dict]) -> List[Dict]:
    def loc_name(n: Dict) -> str:
        ln = n.get("localized-name")
        if isinstance(ln, dict):
            return str(ln.get("value") or "").strip()
        return str(ln or "").strip()

    def build(n: Dict) -> Dict:
        cid = str(n.get("id") or "").strip()
        name = loc_name(n)
        ch = n.get("category")
        children = []
        if isinstance(ch, list):
            children = [build(x) for x in ch if isinstance(x, dict)]
        return {"id": cid, "name": name, "children": children}

    tree: List[Dict] = []
    for n in nodes:
        if isinstance(n, dict):
            node = build(n)
            if node.get("id") and node.get("name"):
                tree.append(node)
    return tree

def _flatten_categories(tree: List[Dict]) -> List[Dict]:
    leaves: List[Dict] = []

    def walk(node: Dict, trail: List[str]):
        if not isinstance(node, dict):
            return
        nid = str(node.get("id", "") or "")
        nm  = str(node.get("name", "") or "")
        children = node.get("children")
        cur = [*trail, nm] if nm else trail
        if isinstance(children, list) and children:
            for ch in children:
                walk(ch, cur)
        else:
            if nid and nm:
                leaves.append({"id": nid, "name": nm, "path": " > ".join([p for p in cur if p])})

    for n in tree:
        walk(n, [])
    return leaves

def _cat_name_by_id(leaves: List[Dict], cid: str) -> str:
    cid = str(cid)
    for c in leaves:
        if str(c.get("id")) == cid:
            return c.get("path") or c.get("name") or ""
    return ""

# простая авто-рекомендация категории, если GPT не дал id
_KEYWORDS = [
    # (слова, приоритетное выражение для пути)
    (["router", "fritz", "wifi", "netzwerk", "wlan"], ["pc", "zubehör", "elektronik", "multimedia"]),
    (["drucker", "scanner"], ["pc", "zubehör"]),
    (["reifen", "felgen", "autoteile"], ["autoteile", "reifen"]),
    (["handy", "iphone", "smartphone"], ["handy", "telefon"]),
    (["kamera", "foto", "objective", "canon", "nikon", "sony"], ["foto"]),
    (["playstation", "xbox", "nintendo", "konsole"], ["konsolen"]),
]

def _suggest_category(leaves: List[Dict], text: str) -> Optional[Dict]:
    t = (text or "").lower()
    best = None
    best_score = 0
    for leaf in leaves:
        path = (leaf.get("path") or leaf.get("name") or "").lower()
        score = 0
        # базовое совпадение слов из названия/описания
        for w in re.findall(r"[a-z0-9äöüß]+", t):
            if w and w in path:
                score += 2
        # усилители по наборам ключей
        for keys, boosts in _KEYWORDS:
            if any(k in t for k in keys):
                score += 3
                if any(b in path for b in boosts):
                    score += 5
        # «электроника» часто подходит
        if "elektronik" in path or "pc" in path:
            score += 1
        if score > best_score:
            best_score = score
            best = leaf
    if best:
        return best

    # запасной: взять что-то из электроники
    for leaf in leaves:
        p = (leaf.get("path") or leaf.get("name") or "").lower()
        if any(x in p for x in ("elektronik", "pc", "zubehör")):
            return leaf
    # вообще самый первый лист
    return leaves[0] if leaves else None

# ====== атрибуты категории ======
def _extract_attribute_schema(meta_payload: dict) -> list[dict]:
    schema: list[dict] = []

    def walk_to_attributes(node):
        if not isinstance(node, dict):
            return None
        if "attributes" in node and isinstance(node["attributes"], dict):
            arr = node["attributes"].get("attribute")
            if isinstance(arr, list):
                return arr
        for v in node.values():
            if isinstance(v, (dict, list)):
                res = walk_to_attributes(v)
                if res is not None:
                    return res
        return None

    attrs = walk_to_attributes(meta_payload) or []
    for a in attrs:
        if not isinstance(a, dict):
            continue
        if str(a.get("group-name", "")).lower() == "badges":
            continue
        name = a.get("name")
        typ = a.get("type")
        write = a.get("write")
        required = (write == "required")
        options = []
        sv = a.get("supported-value")
        if isinstance(sv, list):
            for opt in sv:
                if isinstance(opt, dict) and isinstance(opt.get("value"), str):
                    options.append({"value": opt["value"], "label": opt.get("localized-label", opt["value"])})
        if isinstance(name, str) and isinstance(typ, str):
            schema.append({"name": name, "type": typ, "required": required, "options": options})
    return schema

def _fill_required_attributes(schema: list[dict], base: dict | None = None) -> dict:
    out = dict(base or {})
    for a in schema:
        name = a.get("name")
        if not name or name in out or not a.get("required"):
            continue
        opts = a.get("options") or []
        if opts:
            for opt in opts:
                v = opt.get("value")
                if v not in (None, "", "null"):
                    out[name] = v
                    break
            if name in out:
                continue
        typ = (a.get("type") or "").lower()
        if typ in ("boolean", "bool"):
            out[name] = True
        elif typ in ("integer", "number", "int", "float", "double"):
            out[name] = 1
        else:
            out[name] = "generic"
    return out

# ====== выбор аккаунта ======
async def _fetch_accounts_page(cursor: int, limit: int = 12) -> Tuple[int, Dict]:
    status, data = await sp_list_accounts(cursor=cursor, limit=limit)
    if status != 200 or not isinstance(data, dict):
        return status, {"accounts": [], "hasMore": False, "cursor": 0}
    return status, data

def _kb_accounts(tokens: List[Tuple[str, str]], page_token: Optional[str]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for tok, label in tokens:
        rows.append([InlineKeyboardButton(text=label[:32], callback_data=f"apub:pick:{tok}")])
    nav: List[InlineKeyboardButton] = []
    if page_token:
        nav.append(InlineKeyboardButton(text="Вперёд ⟩", callback_data=f"apub:page:{page_token}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def start_publish_from_archive(call: CallbackQuery, *, archive_path: str, archive_name: str) -> None:
    await call.answer()
    _cleanup()
    arch_tok = _put({"kind": "archive", "path": archive_path, "name": archive_name})

    cursor = 0
    status, data = await _fetch_accounts_page(cursor)
    if status != 200:
        await call.message.answer("Не удалось получить список аккаунтов.")
        return

    accs = data.get("accounts") or []
    next_cursor = data.get("nextCursor") or data.get("cursorNext") or data.get("cursor")
    has_more = bool(data.get("hasMore"))
    page_tok = None
    if has_more and isinstance(next_cursor, int):
        page_tok = _put({"kind": "page", "cursor": next_cursor, "arch": arch_tok})

    btn_tokens: List[Tuple[str, str]] = []
    for a in accs:
        username = a.get("username") or a.get("email")
        if not username:
            continue
        t = _put({"kind": "pick", "username": username, "arch": arch_tok})
        label = f"{username} {'✅' if a.get('loggedIn') else '❌'}{' ✔' if a.get('verified') else ''}"
        btn_tokens.append((t, label))

    kb = _kb_accounts(btn_tokens, page_tok)
    await call.message.answer(
        f"Выбери аккаунт для публикации:\n<b>{archive_name}</b>",
        reply_markup=kb
    )

async def handle_account_page_cb(call: CallbackQuery) -> None:
    await call.answer()
    tok = call.data.split(":", 2)[-1]
    payload = _get(tok)
    if not payload or payload.get("kind") != "page":
        await call.message.answer("Сессия устарела. Начни заново из архива.")
        return
    arch_tok = payload["arch"]
    cursor = int(payload["cursor"])

    status, data = await _fetch_accounts_page(cursor)
    if status != 200:
        await call.message.answer("Не удалось получить следующую страницу аккаунтов.")
        return

    accs = data.get("accounts") or []
    next_cursor = data.get("nextCursor") or data.get("cursorNext") or data.get("cursor")
    has_more = bool(data.get("hasMore"))
    page_tok = None
    if has_more and isinstance(next_cursor, int):
        page_tok = _put({"kind": "page", "cursor": next_cursor, "arch": arch_tok})

    btn_tokens: List[Tuple[str, str]] = []
    for a in accs:
        username = a.get("username") or a.get("email")
        if not username:
            continue
        t = _put({"kind": "pick", "username": username, "arch": arch_tok})
        label = f"{username} {'✅' if a.get('loggedIn') else '❌'}{' ✔' if a.get('verified') else ''}"
        btn_tokens.append((t, label))

    kb = _kb_accounts(btn_tokens, page_tok)
    await call.message.edit_reply_markup(reply_markup=kb)

async def handle_account_pick_cb(call: CallbackQuery) -> None:
    await call.answer()
    tok = call.data.split(":", 2)[-1]
    payload = _get(tok)
    if not payload or payload.get("kind") != "pick":
        await call.message.answer("Сессия устарела. Начни заново из архива.")
        return

    arch = _get(payload["arch"])
    if not arch or arch.get("kind") != "archive":
        await call.message.answer("Сессия архива потеряна. Начни заново.")
        return

    username = payload["username"]
    archive_path = arch["path"]
    archive_name = arch["name"]

    status_ui = await TgStatus(call).start(f"Публикация из архива под {username}")

    # 1) читаем info.txt
    title0, price0, url0, saved0, desc0 = _read_info(archive_path)
    info = {"title": title0, "price": price0, "description": desc0, "url": url0}
    await status_ui.add("Читаю лот из архива", icon="📦")
    await status_ui.code("Исходные данные", info)

    price_type, amount = _price_from_text(price0)

    # 2) категории
    tree = await _fetch_categories_tree()
    leaves = _flatten_categories(tree)
    if not leaves:
        await status_ui.add("Категории не получены (остановил бы процесс, но продолжать нельзя).", ok=False)
        await call.message.answer("Не удалось получить список категорий. Остановил процесс.")
        return

    # 3) GPT: категория, текст, возможно postcode
    gpt = _gpt_fill_ad(info, leaves) if (settings.OPENAI_API_KEY or "").strip() else {}
    title = (gpt.get("title") or title0 or "Kleinanzeige").strip()
    description = (gpt.get("description") or desc0 or (url0 or "Privatverkauf. Abholung nach Absprache.")).strip()

    # ---- выбор категории: сначала GPT, если нет — авто-подбор ----
    category_id = str(gpt.get("category_id") or "").strip()
    picked_by = "GPT"
    if not category_id or not any(c["id"] == category_id for c in leaves):
        text_for_guess = f"{title}\n{description}"
        guess = _suggest_category(leaves, text_for_guess)
        if guess:
            category_id = str(guess["id"])
            picked_by = "auto"
        else:
            # крайне маловероятно, но всё же
            category_id = str(leaves[0]["id"])
            picked_by = "fallback"

    cat_name = _cat_name_by_id(leaves, category_id)
    await status_ui.add(f"Категория выбрана ({'ChatGPT' if picked_by=='GPT' else 'авто'}): {category_id} — {cat_name}", ok=True)

    # 4) фото
    img_files = _extract_images(archive_path)
    img_urls: List[str] = []
    if img_files:
        files_payload = [content for _, content in img_files]
        try:
            ok_up, data_up, st_up = await sp_upload_images(username=username, files=files_payload)
        except Exception as e:
            log.info("upload images exception: %s", e)
            ok_up, data_up, st_up = False, {"error": str(e)}, 0

        if ok_up and isinstance(data_up, list):
            img_urls = [str(u) for u in data_up if isinstance(u, str)]
            await status_ui.add(f"Фото загружены: {len(img_urls)} шт", ok=True)
        else:
            await status_ui.add("Фото не загрузились", ok=False)
            await status_ui.code("Ответ images", data_up)
    else:
        await status_ui.add("Фото в архиве не найдены", icon="🖼")

    # 5) цена
    pt = gpt.get("priceType") or price_type
    amt = gpt.get("amount") or amount

    try:
        amt_str = str(amt).strip().replace("€", "").replace(",", ".")
        if "." in amt_str:
            amt_str = amt_str.split(".")[0]
        amt = float(amt_str)
    except Exception:
        amt = float(amount or 0)

    
    # 6) метаданные категории
    attrs_final: dict = {}
    try:
        ok_meta, meta_payload, status_meta = await sp_get_category_metadata(str(category_id))
        if ok_meta and isinstance(meta_payload, dict):
            schema = _extract_attribute_schema(meta_payload)

            # атрибуты, что могли прийти от GPT
            attrs_from_gpt = gpt.get("attributes") if isinstance(gpt.get("attributes"), dict) else {}
            attrs_final = dict(attrs_from_gpt)

            # 6.1) *.versand = "ja", если атрибут существует
            versand_attr = next((a for a in schema if a.get("name", "").endswith(".versand")), None)
            if versand_attr:
                attrs_final[versand_attr["name"]] = "ja"

	    # 6.1.1) *.condition = "like_new" (Sehr gut)
	    condition_attr = next((a for a in schema if a.get("name", "").endswith(".condition")), None)
	    if condition_attr:
	        attrs_final[condition_attr["name"]] = "like_new"

            # 6.2) *.art — выбираем через ChatGPT из supported-value
            art_attr = next((a for a in schema if a.get("name", "").endswith(".art")), None)
            if art_attr:
                art_name = art_attr["name"]

                # Нормализуем options к списку строк
                raw_opts = art_attr.get("options", []) or []
                if raw_opts and isinstance(raw_opts[0], dict):
                    art_options = [o.get("value") for o in raw_opts if isinstance(o, dict) and o.get("value")]
                else:
                    art_options = [str(o) for o in raw_opts]

                if art_name not in attrs_final and art_options:
                    picked = _choose_art_via_gpt(art_options, title, description)
                    if not picked:
                        for fb in ("sonstiges", "weiteres", "andere", "zubehoer"):
                            if fb in art_options:
                                picked = fb
                                break
                        if not picked:
                            picked = art_options[0]
                    attrs_final[art_name] = picked

            # 6.3) подстраховка: если есть ещё обязательные ENUM — заполним допустимым значением
            for a in schema:
                n = a.get("name")
                if not n or n in attrs_final:
                    continue
                opts = a.get("options") or []
                if opts:
                    if isinstance(opts[0], dict):
                        opts = [o.get("value") for o in opts if isinstance(o, dict) and o.get("value")]
                    attrs_final[n] = opts[0]

        else:
            log.info("metadata fetch fail: %s %s", ok_meta, str(meta_payload)[:400])
            await status_ui.add("Метаданные не подтянулись, иду дальше", icon="⚙")
            attrs_final = gpt.get("attributes") if isinstance(gpt.get("attributes"), dict) else {}
    except Exception as e:
        log.info("metadata error: %s", e)
        attrs_final = gpt.get("attributes") if isinstance(gpt.get("attributes"), dict) else {}

    # 7) индекс
    postcode = str(gpt.get("postcode") or "").strip() or _random_postcode()

    # 8) публикация
    payload_preview = {
        "account": username,
        "contact": "Privat",
        "postcode": postcode,
        "title": title,
        "categoryId": category_id,
        "priceType": (pt if pt in ("SPECIFIED_AMOUNT", "PLEASE_CONTACT")
                      else ("SPECIFIED_AMOUNT" if amt else "PLEASE_CONTACT")),
        "amount": float(amt or 0),
        "images": len(img_urls),
        "shippingOptions": ["DHL_002"],
        "shippingPrice": 0,
        "threatMetrix": True,
    }
    await status_ui.add("Публикую объявление…", icon="🚀")
    await status_ui.code("Payload", payload_preview)

    res_pub_ok, resp_pub, status_pub = await sp_publish_ad(
        account=username,
        contact="Privat",
        postcode=postcode,
        title=title,
        description=description,
        category_id=category_id,
        price_type=payload_preview["priceType"],
        imprint="",
        amount=float(amt or 0),
        attributes=attrs_final,
        images=img_urls,
        shipping_options=["DHL_002"],
        shipping_price=0,
        threat_metrix=True,
    )

    if res_pub_ok:
        await status_ui.add("Объявление опубликовано", ok=True)
        await status_ui.code("Ответ SuitePro", resp_pub)
    else:
        await status_ui.add(f"Не удалось опубликовать, HTTP {status_pub}", ok=False)
        await status_ui.code("Ответ SuitePro", resp_pub)
        await call.message.answer("❌ Публикация не удалась. Смотри лог выше.")
