# app/services/add.py
# -*- coding: utf-8 -*-
"""
Async-клиент SuitePro:
- add_ad(payload)
- upload_images(username, files)
- list_categories()
- get_category_metadata(category_id)
- publish_ad(...) — фасад
"""
from __future__ import annotations

import asyncio
import time
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import urljoin, urlencode

import aiohttp

from app.config import settings

log = logging.getLogger("klazfiler.suitepro")

JsonDict = Dict[str, Any]
Result = Tuple[bool, Any, int]

# ---------- URL helpers ----------

_MAX_LOG = 8000
def _pretty(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)

def _base(api_path: str) -> str:
    base = (getattr(settings, "SUITEPRO_API_URL", "") or "").rstrip("/") + "/"
    return urljoin(base, api_path.lstrip("/"))

def _classifieds_url() -> str:
    return (getattr(settings, "SUITEPRO_CLASSIFIEDS_URL", "") or _base("classifieds/")).rstrip("/") + "/"

def _images_url() -> str:
    return (getattr(settings, "SUITEPRO_IMAGES_URL", "") or _base("classifieds/images")).rstrip("/")

def _categories_url() -> str:
    return (getattr(settings, "SUITEPRO_CATEGORIES_URL", "") or _base("categories")).rstrip("/")

def _metadata_url() -> str:
    return (getattr(settings, "SUITEPRO_METADATA_URL", "") or _base("metadata")).rstrip("/")

# ---------- auth/timeout/retry ----------
def _headers_json() -> Dict[str, str]:
    api_key = (getattr(settings, "SUITEPRO_API_KEY", "") or "").strip()
    scheme = (getattr(settings, "SUITEPRO_AUTH_SCHEME", "Bearer") or "Bearer").strip().lower()
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if scheme == "x-api-key":
        h["X-API-Key"] = api_key
    else:
        h["Authorization"] = f"Bearer {api_key}"
    return h

def _headers_multipart() -> Dict[str, str]:
    h = _headers_json().copy()
    h.pop("Content-Type", None)
    return h

def _timeout() -> int:
    try:
        return int(getattr(settings, "API_TIMEOUT", 60))
    except Exception:
        return 60

def _retries() -> int:
    try:
        return int(getattr(settings, "API_RETRIES", 3))
    except Exception:
        return 3

async def _req_with_retry(session: aiohttp.ClientSession, method: str, url: str, **kwargs) -> Tuple[int, Any]:
    retries = _retries()
    backoff = 1.0
    last_exc: Optional[BaseException] = None

    # Лог исходящего запроса
    out_json = kwargs.get("json")
    out_data = kwargs.get("data")
    if out_json is not None:
        log.info("[HTTP-REQ] %s %s\n%s", method, url, _pretty(out_json)[:_MAX_LOG])
    elif out_data is not None:
        log.info("[HTTP-REQ] %s %s (multipart/form-data)", method, url)
    else:
        log.info("[HTTP-REQ] %s %s", method, url)

    for attempt in range(1, retries + 2):
        t0 = time.monotonic()
        try:
            async with session.request(method, url, timeout=_timeout(), **kwargs) as resp:
                dt = time.monotonic() - t0
                status = resp.status
                text = await resp.text()

                try:
                    data = json.loads(text) if text else {}
                except Exception:
                    data = text

                log.info("[HTTP-RESP] %s %s -> %s in %.3fs", method, url, status, dt)
                # Тело ответа
                body = data if isinstance(data, (dict, list)) else text
                if body:
                    log.info("[HTTP-BODY]\n%s", _pretty(body)[:_MAX_LOG])

                if 200 <= status < 300:
                    return status, data
                if 400 <= status < 500 and status != 429:
                    return status, data

                log.warning("HTTP %s %s -> %s, retry %s/%s", method, url, status, attempt, retries+1)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exc = e
            log.warning("HTTP %s %s exception: %s, retry %s/%s", method, url, e, attempt, retries+1)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 10)

    return -1, {"error": "request failed", "exception": str(last_exc) if last_exc else None}


# ---------- core calls ----------
async def add_ad(payload: JsonDict) -> Result:
    required = ["account", "contact", "postcode", "title", "description", "categoryId", "priceType"]
    missing = [k for k in required if not payload.get(k)]
    if missing:
        log.error("[Publish] missing required: %s", ", ".join(missing))
        return False, {"error": f"missing required fields: {', '.join(missing)}"}, 0

    if isinstance(payload.get("title"), str):
        payload["title"] = payload["title"].strip()[:65]

    clean = {k: v for k, v in payload.items() if v is not None}

    log.info("[Publish] payload:")
    for k in ("account", "contact", "postcode", "title", "categoryId", "priceType", "amount"):
        if k in clean:
            log.info("[Publish]  %s = %s", k, clean[k])
    log.info("[Publish]  images = %s", len(clean.get("images") or []))
    if "shippingOptions" in clean:
        log.info("[Publish]  shippingOptions = %s", clean["shippingOptions"])
    if "shippingPrice" in clean:
        log.info("[Publish]  shippingPrice = %s", clean["shippingPrice"])
    if "threatMetrix" in clean:
        log.info("[Publish]  threatMetrix = %s", clean["threatMetrix"])

    url = _classifieds_url()
    headers = _headers_json()
    log.info("[Publish] POST %s", url)

    async with aiohttp.ClientSession(headers=headers) as sess:
        status, data = await _req_with_retry(sess, "POST", url, json=clean)

    ok = bool(isinstance(data, dict) and data.get("added") is True and 200 <= status < 300)
    log.info("[Publish] result ok=%s status=%s", ok, status)
    return ok, data, status


async def upload_images(username: Optional[str], files: Iterable[Union[str, bytes]]) -> Result:
    """POST /classifieds/images"""
    url = _images_url()
    headers = _headers_multipart()

    form = aiohttp.FormData()
    if username:
        form.add_field("username", username)

    idx = 0
    for f in files:
        if isinstance(f, str):
            form.add_field("images", open(f, "rb"), filename=f.split("/")[-1].split("\\")[-1], content_type="image/jpeg")
        else:
            form.add_field("images", f, filename=f"img_{idx}.jpg", content_type="image/jpeg")
        idx += 1

    async with aiohttp.ClientSession(headers=headers) as sess:
        status, data = await _req_with_retry(sess, "POST", url, data=form)

    ok = bool(status == 200 and isinstance(data, list))
    if ok:
        log.info("[Images] uploaded %s images", len(data))
    else:
        log.info("[Images] upload failed: %s %s", status, str(data)[:400])
    return ok, data, status

async def list_categories() -> Result:
    url = _categories_url()
    headers = _headers_json()
    async with aiohttp.ClientSession(headers=headers) as sess:
        status, data = await _req_with_retry(sess, "GET", url)
    ok = bool(200 <= status < 300 and isinstance(data, list))
    return ok, data, status

async def get_category_metadata(category_id: str) -> Result:
    if not category_id:
        return False, {"error": "category_id is required"}, 0
    base = _metadata_url()
    sep = "&" if "?" in base else "?"
    url = f"{base}{sep}{urlencode({'id': category_id})}"
    headers = _headers_json()
    async with aiohttp.ClientSession(headers=headers) as sess:
        status, data = await _req_with_retry(sess, "GET", url)
    ok = bool(200 <= status < 300 and isinstance(data, dict))
    return ok, data, status

# ---------- фасад ----------
async def publish_ad(
    account: str,
    contact: str,
    postcode: str,
    title: str,
    description: str,
    category_id: str,
    price_type: str,
    *,
    imprint: str = "",
    amount: Optional[float] = None,
    attributes: Optional[JsonDict] = None,
    images: Optional[List[str]] = None,
    shipping_options: Optional[List[str]] = None,
    shipping_price: Optional[int] = None,
    threat_metrix: Optional[bool] = None,
) -> Result:
    payload: JsonDict = {
        "account": account,
        "contact": contact,
        "postcode": postcode,
        "title": title,
        "description": description,
        "categoryId": category_id,          # ✅ исправлено
        "priceType": price_type,            # ✅ исправлено
        "imprint": imprint,
        "amount": float(amount or 0),       # ✅ float
        "attributes": attributes or {},
        "images": images or [],
        "shippingOptions": shipping_options or [],  # ✅ исправлено
        "shippingPrice": shipping_price or 0,       # ✅ исправлено
        "threatMetrix": threat_metrix is True,       # ✅ исправлено
    }
    return await add_ad(payload)
