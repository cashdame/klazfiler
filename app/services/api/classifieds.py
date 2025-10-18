# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import logging
from typing import Any, Dict, List, Tuple, Iterable

import aiohttp
from app.config import settings

log = logging.getLogger("klazfiler.classifieds")

API_TIMEOUT = int(getattr(settings, "API_TIMEOUT", 120))
API_RETRIES = int(getattr(settings, "API_RETRIES", 3))

API_BASE = settings.SUITEPRO_API_URL.rstrip("/")
SUITEPRO_API_KEY = (settings.SUITEPRO_API_KEY or "").strip()

CATEGORIES_URL   = getattr(settings, "categories_url",   f"{API_BASE}/categories")
METADATA_URL     = getattr(settings, "metadata_url",     f"{API_BASE}/metadata")
CLASSIFIEDS_URL  = getattr(settings, "classifieds_url",  f"{API_BASE}/classifieds/")
IMAGES_UPLOAD_URL= getattr(settings, "images_upload_url",f"{API_BASE}/classifieds/images")

HEADERS_JSON = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-API-Key": SUITEPRO_API_KEY,
    "User-Agent": "klazfiler/classifieds",
}
HEADERS_AUTH = {
    "X-API-Key": SUITEPRO_API_KEY,
    "User-Agent": "klazfiler/classifieds",
}

RETRY_STATUSES = {429, 500, 502, 503, 504}

async def _req_with_retry(method: str, url: str, *, json_body=None, params=None, data=None, form=None, headers=None) -> Tuple[int, Any]:
    delay = 1.0
    for attempt in range(1, API_RETRIES + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers or {}) as s:
                kwargs = {}
                if params:    kwargs["params"] = params
                if form:      kwargs["data"]   = form
                elif data:    kwargs["data"]   = data
                elif json_body is not None:
                    kwargs["json"] = json_body

                async with s.request(method.upper(), url, **kwargs) as r:
                    ct = (r.headers.get("content-type") or "").lower()
                    text = await r.text()
                    if r.status >= 400:
                        log.info("[classif] %s %s -> %s; body: %s", method, url, r.status, text[:1000])
                    if r.status in RETRY_STATUSES and attempt < API_RETRIES:
                        await aiohttp.asyncio.sleep(delay)
                        delay = min(delay * 2, 10)
                        continue
                    if "json" in ct:
                        try:
                            return r.status, json.loads(text)
                        except Exception:
                            return r.status, text
                    return r.status, text
        except Exception as e:
            if attempt < API_RETRIES:
                await aiohttp.asyncio.sleep(delay)
                delay = min(delay * 2, 10)
                continue
            log.exception("[classif] request failed: %s %s", method, url)
            raise
    return 0, {"message": "unknown error"}

# ---- API ----

async def add_ad(payload: Dict[str, Any]) -> Tuple[int, Any]:
    return await _req_with_retry("POST", CLASSIFIEDS_URL, json_body=payload, headers=HEADERS_JSON)

async def upload_images(username: str, files: Iterable[tuple[str, bytes]]) -> Tuple[int, List[str] | Any]:
    """
    files: iterable of (filename, file_bytes)
    """
    form = aiohttp.FormData()
    form.add_field("username", username)
    for fname, content in files:
        ext = os.path.splitext(fname)[1].lower()
        ctype = "image/jpeg"
        if ext == ".png":   ctype = "image/png"
        elif ext == ".gif": ctype = "image/gif"
        elif ext == ".webp": ctype = "image/webp"
        form.add_field("images", content, filename=fname, content_type=ctype)

    status, data = await _req_with_retry("POST", IMAGES_UPLOAD_URL, form=form, headers=HEADERS_AUTH)
    return status, data

async def get_category_metadata(category_id: str) -> Tuple[int, Any]:
    return await _req_with_retry("GET", METADATA_URL, params={"id": category_id}, headers=HEADERS_JSON)
