# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any, Tuple

import aiohttp
from app.config import settings

API_TIMEOUT = int(getattr(settings, "API_TIMEOUT", 120))
API_RETRIES = int(getattr(settings, "API_RETRIES", 3))

API_BASE = settings.SUITEPRO_API_URL.rstrip("/")
SUITEPRO_API_KEY = (settings.SUITEPRO_API_KEY or "").strip()
CATEGORIES_URL = getattr(settings, "categories_url", f"{API_BASE}/categories")

HEADERS_JSON = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-API-Key": SUITEPRO_API_KEY,
    "User-Agent": "klazfiler/categories",
}

RETRY_STATUSES = {429, 500, 502, 503, 504}

async def _req_with_retry(method: str, url: str, *, headers=None) -> Tuple[int, Any]:
    delay = 1.0
    for attempt in range(1, API_RETRIES + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers or {}) as s:
                async with s.request(method.upper(), url) as r:
                    ct = (r.headers.get("content-type") or "").lower()
                    text = await r.text()
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
        except Exception:
            if attempt < API_RETRIES:
                await aiohttp.asyncio.sleep(delay)
                delay = min(delay * 2, 10)
                continue
            raise
    return 0, {"message": "unknown error"}

async def list_categories() -> Tuple[int, Any]:
    return await _req_with_retry("GET", CATEGORIES_URL, headers=HEADERS_JSON)
