from __future__ import annotations

import aiohttp
from typing import Any, Tuple

from app.config import settings

BASE = settings.SUITEPRO_API_URL.rstrip("/")
ACCOUNTS_URL = f"{BASE}/accounts"
ADD_TOKENS_URL = f"{BASE}/accounts/tokens"


def _headers() -> dict[str, str]:
    key = (settings.SUITEPRO_API_KEY or "").strip()
    return {
        "X-API-Key": key,              # ключевой заголовок
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "klazfiler/1.0",
    }


async def list_accounts(
    cursor: int = 0,
    limit: int = 12,
    search: str | None = None,
    login_status: str | None = None,
    sort_by: str = "creationDate",      # Сортировка по дате создания
    sort_order: str = "asc"             # От старых к новым (asc = ascending)
) -> Tuple[int, Any]:
    """
    Получает список аккаунтов с пагинацией.
    
    Args:
        cursor: Позиция для пагинации
        limit: Количество аккаунтов на страницу
        search: Поиск по username
        login_status: Фильтр по статусу входа
        sort_by: Поле для сортировки (creationDate, username, id, adsCount, etc.)
        sort_order: Порядок сортировки ("asc" = от старых к новым, "desc" = от новых к старым)
    
    Returns:
        Tuple[status_code, response_data]
    
    По умолчанию: сортирует от СТАРЫХ к НОВЫМ по дате создания
    """
    params = {
        "cursor": cursor,
        "limit": limit,
        "sortBy": sort_by,
        "sortOrder": sort_order
    }
    
    # Добавляем опциональные параметры
    if search:
        params["search"] = search
    if login_status:
        params["loginStatus"] = login_status
    
    timeout = aiohttp.ClientTimeout(total=settings.API_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as s:
            async with s.get(ACCOUNTS_URL, params=params) as r:
                ct = (r.headers.get("content-type") or "").lower()
                data = await (r.json() if "json" in ct else r.text())
                return r.status, data
    except Exception as e:
        return 0, {"message": f"network error: {e}"}


async def add_account_tokens(
    *,
    username: str,
    accessToken: str,
    refreshToken: str,
    userIdToken: str,
    proxyURL: str | None = None,
) -> Tuple[int, Any]:
    payload = {
        "username": username,
        "accessToken": accessToken,
        "refreshToken": refreshToken,
        "userIdToken": userIdToken,
    }
    if proxyURL:
        payload["proxyURL"] = proxyURL

    timeout = aiohttp.ClientTimeout(total=settings.API_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as s:
            async with s.post(ADD_TOKENS_URL, json=payload) as r:
                ct = (r.headers.get("content-type") or "").lower()
                data = await (r.json() if "json" in ct else r.text())
                return r.status, data
    except Exception as e:
        return 0, {"message": f"network error: {e}"}
