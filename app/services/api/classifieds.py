# app/services/api/classifieds.py
# -*- coding: utf-8 -*-
import asyncio
import json
import logging
from typing import Any, Dict, List, Tuple, Optional

import aiohttp

from app.config import settings

log = logging.getLogger("klazfiler.classifieds")

BASE = (settings.SUITEPRO_API_URL or "https://api.suitepro.to").rstrip("/")
CLASSIFIEDS_URL = f"{BASE}/classifieds/"
IMAGES_URL = f"{BASE}/classifieds/images"
METADATA_URL = f"{BASE}/metadata"
HEADERS_JSON = {
    "Authorization": f"Bearer {settings.SUITEPRO_API_KEY}",
    "Content-Type": "application/json",
}
HEADERS_FORM = {
    "Authorization": f"Bearer {settings.SUITEPRO_API_KEY}",
}

async def _req_with_retry(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    data: Any = None,
    headers: Optional[Dict[str, str]] = None,
    retries: int = None,
    timeout_sec: int = None,
) -> Tuple[int, Any]:
    if retries is None:
        retries = int(getattr(settings, "API_RETRIES", 3) or 3)
    if timeout_sec is None:
        timeout_sec = int(getattr(settings, "API_TIMEOUT", 120) or 120)

    for attempt in range(retries + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_sec)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
                async with s.request(method, url, params=params, json=json_body, data=data) as r:
                    ct = (r.headers.get("content-type") or "").lower()
                    text = await r.text()
                    if r.status >= 400:
                        # лог тела ошибки, это очень помогает
                        try:
                            body = json.loads(text)
                            log.info("[classif] %s %s -> %s; body: %s", method, url, r.status, json.dumps(body, ensure_ascii=False))
                        except Exception:
                            log.info("[classif] %s %s -> %s; body: %s", method, url, r.status, text[:500])
                    if "json" in ct:
                        data = json.loads(text)
                    else:
                        data = text
                    if r.status in (429, 500, 502, 503, 504) and attempt < retries:
                        delay = 2.0 ** attempt
                        await asyncio.sleep(delay)
                        continue
                    return r.status, data
        except Exception as e:
            if attempt < retries:
                delay = 1.0 + attempt
                log.info("[classif] retry %s after error: %s; sleep %.1fs", url, e, delay)
                await asyncio.sleep(delay)
                continue
            return 0, {"message": f"network error: {e}"}
    return 0, {"message": "unexpected"}

async def upload_images(*, username: str, files: List[Tuple[str, bytes]]) -> Tuple[int, Any]:
    form = aiohttp.FormData()
    form.add_field("username", username)
    for i, (fname, content) in enumerate(files):
        form.add_field(
            "images",
            content,
            filename=fname,
            content_type="application/octet-stream",
        )
    return await _req_with_retry("POST", IMAGES_URL, data=form, headers=HEADERS_FORM)

async def get_category_metadata(category_id: str) -> Tuple[int, Any]:
    params = {"id": str(category_id)}
    return await _req_with_retry("GET", METADATA_URL, params=params, headers=HEADERS_JSON)

async def add_ad(payload: Dict[str, Any]) -> Tuple[int, Any]:
    # страхуемся по лимиту заголовка на стороне клиента
    title = (payload.get("title") or "").strip()
    if len(title) > 65:
        payload = dict(payload)
        payload["title"] = title[:65]
    return await _req_with_retry("POST", CLASSIFIEDS_URL, json_body=payload, headers=HEADERS_JSON)
