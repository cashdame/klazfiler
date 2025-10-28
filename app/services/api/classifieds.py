# app/services/api/classifieds.py
# -*- coding: utf-8 -*-
from __future__ import annotations

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


def _headers_json() -> Dict[str, str]:
    """
    Как раньше в klazfiler.suitepro: поддерживаем X-API-Key и Bearer.
    Управляется переменной settings.SUITEPRO_AUTH_SCHEME:
      - "x-api-key"  -> заголовок X-API-Key: <key>
      - "bearer"     -> заголовок Authorization: Bearer <key>
    По умолчанию считаем Bearer, если не указано иное.
    """
    key = (getattr(settings, "SUITEPRO_API_KEY", "") or "").strip()
    scheme = (getattr(settings, "SUITEPRO_AUTH_SCHEME", "bearer") or "bearer").strip().lower()

    h: Dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if scheme in ("x-api-key", "api-key"):
        h["X-API-Key"] = key
    else:
        h["Authorization"] = f"Bearer {key}"
    return h


def _headers_form() -> Dict[str, str]:
    # multipart сам проставит boundary/Content-Type
    h = _headers_json().copy()
    h.pop("Content-Type", None)
    return h


async def _req_with_retry(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    data: Any = None,
    headers: Optional[Dict[str, str]] = None,
    retries: Optional[int] = None,
    timeout_sec: Optional[int] = None,
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
                        # лог тела ошибки оставляем — это реально помогает дебажить
                        try:
                            body = json.loads(text)
                            log.info("[classif] %s %s -> %s; body: %s", method, url, r.status, json.dumps(body, ensure_ascii=False))
                        except Exception:
                            log.info("[classif] %s %s -> %s; body: %s", method, url, r.status, text[:500])

                    if "json" in ct:
                        data_parsed = json.loads(text)
                    else:
                        data_parsed = text

                    if r.status in (429, 500, 502, 503, 504) and attempt < retries:
                        delay = 2.0 ** attempt
                        await asyncio.sleep(delay)
                        continue

                    return r.status, data_parsed

        except Exception as e:
            if attempt < retries:
                delay = 1.0 + attempt
                log.info("[classif] retry %s after error: %s; sleep %.1fs", url, e, delay)
                await asyncio.sleep(delay)
                continue
            return 0, {"message": f"network error: {e}"}

    return 0, {"message": "unexpected"}


async def upload_images(*, username: str, files: List[Tuple[str, bytes]]) -> Tuple[int, Any]:
    """
    files должен быть списком (filename, bytes). Это совпадает с тем, как это делалось раньше.
    """
    form = aiohttp.FormData()
    form.add_field("username", username)
    for fname, content in files:
        form.add_field(
            "images",
            content,
            filename=fname,
            content_type="application/octet-stream",
        )
    return await _req_with_retry("POST", IMAGES_URL, data=form, headers=_headers_form())


async def get_category_metadata(category_id: str) -> Tuple[int, Any]:
    """
    Оставляем только metadata, как ты просил (для *.art и атрибутов).
    """
    params = {"id": str(category_id)}
    return await _req_with_retry("GET", METADATA_URL, params=params, headers=_headers_json())


async def add_ad(payload: Dict[str, Any]) -> Tuple[int, Any]:
    """
    Публикация. Страхуем длину title, как было, и отправляем с правильными заголовками.
    """
    # ═══════════════════════════════════════════════════════════════
    # ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ PAYLOAD перед отправкой
    # ═══════════════════════════════════════════════════════════════
    log.info("=" * 80)
    log.info("[SUITEPRO-ADD-AD] Preparing to POST to /classifieds/")
    log.info("[SUITEPRO-ADD-AD] Full payload:")
    log.info(json.dumps(payload, ensure_ascii=False, indent=2))
    log.info("-" * 80)
    log.info("[SUITEPRO-ADD-AD] Payload details:")
    log.info("  account: %s", payload.get("account"))
    log.info("  contact: %s", payload.get("contact"))
    log.info("  categoryId: %s (type: %s)", payload.get("categoryId"), type(payload.get("categoryId")).__name__)
    log.info("  title: %s (len: %d)", payload.get("title"), len(payload.get("title", "")))
    log.info("  priceType: %s", payload.get("priceType"))
    log.info("  amount: %s (type: %s)", payload.get("amount"), type(payload.get("amount")).__name__)
    log.info("  postcode: %s", payload.get("postcode"))
    log.info("  images: %d items", len(payload.get("images", [])))
    if payload.get("images"):
        for i, img in enumerate(payload.get("images", []), 1):
            log.info("    image %d: %s", i, img[:80] if len(img) > 80 else img)
    log.info("  shippingOptions: %s", payload.get("shippingOptions"))
    log.info("  shippingPrice: %s (type: %s)", payload.get("shippingPrice"), type(payload.get("shippingPrice")).__name__)
    log.info("  attributes: %d keys", len(payload.get("attributes", {})))
    for k, v in (payload.get("attributes") or {}).items():
        log.info("    %s = %s", k, v)
    log.info("  adAddress: %s", payload.get("adAddress"))
    log.info("  adType: %s", payload.get("adType"))
    log.info("  id: %s", payload.get("id"))
    log.info("  handshake: %s (type: %s)", payload.get("handshake"), type(payload.get("handshake")).__name__)
    log.info("  threatMetrix: %s (type: %s)", payload.get("threatMetrix"), type(payload.get("threatMetrix")).__name__)
    log.info("=" * 80)
    # ═══════════════════════════════════════════════════════════════
    
    title = (payload.get("title") or "").strip()
    if len(title) > 65:
        payload = dict(payload)
        payload["title"] = title[:65]
        log.info("[SUITEPRO-ADD-AD] Title truncated to 65 chars: %s", payload["title"])

    result = await _req_with_retry("POST", CLASSIFIEDS_URL, json_body=payload, headers=_headers_json())
    
    # Логируем результат
    status, data = result
    log.info("[SUITEPRO-ADD-AD] Response status: %d", status)
    if status >= 400:
        log.error("[SUITEPRO-ADD-AD] ❌ REQUEST FAILED!")
        log.error("[SUITEPRO-ADD-AD] Error response: %s", json.dumps(data, ensure_ascii=False))
    else:
        log.info("[SUITEPRO-ADD-AD] ✅ Success! Response: %s", json.dumps(data, ensure_ascii=False))
    
    return result
