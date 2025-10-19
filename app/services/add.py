# app/services/suitepro_client.py
# -*- coding: utf-8 -*-
"""
Async‑клиент под SuitePro API с функцией публикации объявления и вспомогательными методами:
- add_ad(payload)
- upload_images(username, files)
- list_categories()
- get_category_metadata(category_id)

Особенности:
- aiohttp, экспоненциальные ретраи на 5xx/429/сетевых ошибках.
- Авторизация: Bearer (по умолчанию) или X-API-Key через settings.SUITEPRO_AUTH_SCHEME.
- Базовые URL: берём из settings: SUITEPRO_CLASSIFIEDS_URL/IMAGES_URL/CATEGORIES_URL/METADATA_URL,
  иначе строим от SUITEPRO_API_URL.
- Лёгкая валидация payload для Add Ad, чистка None, обрезка title до 65 символов.
- Унифицированный ответ: (ok: bool, data: dict|list|str|None, status: int).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import urljoin, urlencode

import aiohttp

from app.config import settings

log = logging.getLogger("klazfiler.suitepro")

JsonDict = Dict[str, Any]
Result = Tuple[bool, Any, int]

# -------------------- URL helpers --------------------

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


# -------------------- auth/timeout/retries --------------------

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
    # Уберём Content-Type, его выставит aiohttp при multipart
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

    for attempt in range(retries + 1):
        try:
            async with session.request(method, url, timeout=_timeout(), **kwargs) as resp:
                status = resp.status
                text = await resp.text()
                try:
                    data = json.loads(text) if text else (await resp.read() or None)
                except Exception:
                    data = text

                if 200 <= status < 300:
                    return status, data
                # 4xx не ретраим, кроме 429
                if 400 <= status < 500 and status != 429:
                    return status, data
                log.warning("HTTP %s %s -> %s, attempt %s/%s", method, url, status, attempt + 1, retries)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exc = e
            log.warning("HTTP %s %s exception: %s, attempt %s/%s", method, url, e, attempt + 1, retries)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 10)

    # Все попытки исчерпаны
    return -1, {"error": "request failed", "exception": str(last_exc) if last_exc else None}


# -------------------- core calls --------------------

async def add_ad(payload: JsonDict) -> Result:
    """POST /classifieds/ — публикация объявления.
    Обязательные поля: account, contact, postcode, title, description, categoryId, priceType.
    priceType: SPECIFIED_AMOUNT или PLEASE_CONTACT. Если SPECIFIED_AMOUNT — желательно amount.
    Возвращает (ok, data, status) где ok = body.get("added") is True.
    """
    required = ["account", "contact", "postcode", "title", "description", "categoryId", "priceType"]
    missing = [k for k in required if not payload.get(k)]
    if missing:
        return False, {"error": f"missing required fields: {', '.join(missing)}"}, 0

    pt = payload.get("priceType")
    if pt not in ("SPECIFIED_AMOUNT", "PLEASE_CONTACT"):
        return False, {"error": "priceType must be 'SPECIFIED_AMOUNT' or 'PLEASE_CONTACT'"}, 0

    # Обрезаем title до 65 символов (часто требуется этим API)
    if isinstance(payload.get("title"), str):
        payload["title"] = payload["title"].strip()[:65]

    # Уберём None
    clean = {k: v for k, v in payload.items() if v is not None}

    url = _classifieds_url()  # оканчивается на /classifieds/
    headers = _headers_json()

    async with aiohttp.ClientSession(headers=headers) as sess:
        status, data = await _req_with_retry(sess, "POST", url, json=clean)

    ok = bool(isinstance(data, dict) and data.get("added") is True and 200 <= status < 300)
    return ok, data, status


async def upload_images(username: Optional[str], files: Iterable[Union[str, bytes]]) -> Result:
    """POST /classifieds/images — загрузка изображений.
    files: список путей к файлам или байтов.
    Возвращает (ok, [urls], status).
    """
    url = _images_url()
    headers = _headers_multipart()

    form = aiohttp.FormData()
    if username:
        form.add_field("username", username)

    idx = 0
    for f in files:
        if isinstance(f, str):
            # путь к файлу
            form.add_field("images", open(f, "rb"), filename=f.split("/")[-1].split("\\")[-1], content_type="image/jpeg")
        else:
            form.add_field("images", f, filename=f"img_{idx}.jpg", content_type="image/jpeg")
        idx += 1

    async with aiohttp.ClientSession(headers=headers) as sess:
        status, data = await _req_with_retry(sess, "POST", url, data=form)

    ok = bool(status == 200 and isinstance(data, list))
    return ok, data, status


async def list_categories() -> Result:
    """GET /categories — список категорий."""
    url = _categories_url()
    headers = _headers_json()
    async with aiohttp.ClientSession(headers=headers) as sess:
        status, data = await _req_with_retry(sess, "GET", url)
    ok = bool(200 <= status < 300 and isinstance(data, list))
    return ok, data, status


async def get_category_metadata(category_id: str) -> Result:
    """GET /metadata?id=... — метаданные категории."""
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


# -------------------- удобный фасад публикации --------------------

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
    """
    Высокоуровневая обёртка над add_ad: принимает параметры по отдельности,
    собирает payload и вызывает add_ad.
    """
    payload: JsonDict = {
        "account": account,
        "contact": contact,
        "postcode": postcode,
        "title": title,
        "description": description,
        "categoryId": category_id,
        "priceType": price_type,
        "imprint": imprint,
        "amount": amount,
        "attributes": attributes or {},
        "images": images or [],
        "shippingOptions": shipping_options or [],
        "shippingPrice": shipping_price,
        "threatMetrix": threat_metrix,
    }
    return await add_ad(payload)


# -------------------- примеры использования --------------------
if __name__ == "__main__":
    async def _demo():
        ok1, cats, s1 = await list_categories()
        print("categories:", ok1, s1, type(cats))

        ok2, meta, s2 = await get_category_metadata("230")
        print("metadata:", ok2, s2, isinstance(meta, dict))

        # Загрузка локальных файлов
        # ok3, urls, s3 = await upload_images("cash", ["/path/to/1.jpg", "/path/to/2.jpg"])
        # print("images:", ok3, s3, urls)

        ok4, data4, s4 = await publish_ad(
            account="acc01",
            contact="+4912345678",
            postcode="20095",
            title="Demo title that may be trimmed to sixty five characters max :)",
            description="Demo desc",
            category_id="240",
            price_type="PLEASE_CONTACT",
            imprint="",
            attributes={"condition": "used"},
            images=["https://domain.com/img1.jpg"],
        )
        print("publish:", ok4, s4, data4)

    asyncio.run(_demo())
