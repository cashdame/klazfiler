# app/services/add.py
"""
Совместимый фасад вокруг app/services/api/classifieds.py
Возвращает (ok: bool, data: Any, status: int), чтобы не ломать вызовы.
"""

from __future__ import annotations
from typing import Any, Dict, List, Tuple

# Настройки берём отсюда, а не из app.core
from app.config import settings  # noqa: F401  (оставляем для совместимости)

# Реальный клиент SuitePro
from app.services.api.classifieds import (
    get_category_metadata as _get_category_metadata_raw,
    upload_images as _upload_images_raw,
    add_ad as _add_ad_raw,
)

Result = Tuple[bool, Any, int]


async def get_category_metadata(category_id: str | int) -> Result:
    status, data = await _get_category_metadata_raw(str(category_id))
    ok = 200 <= int(status) < 300
    return ok, data, status


async def upload_images(*, username: str, files: List[Tuple[str, bytes]]) -> Result:
    """
    Ожидает files как List[(filename, content_bytes)].
    Если вдруг прилетит List[bytes], аккуратно пронумеруем.
    """
    norm_files: List[Tuple[str, bytes]] = []
    if files and isinstance(files[0], tuple):
        norm_files = files  # уже норм
    else:
        for i, content in enumerate(files or [], 1):
            norm_files.append((f"image_{i}.jpg", content))
    status, data = await _upload_images_raw(username=username, files=norm_files)
    ok = 200 <= int(status) < 300
    return ok, data, status


async def publish_ad(
    *,
    account: str,
    contact: str,
    postcode: str,
    title: str,
    description: str,
    category_id: str | int,
    price_type: str,
    imprint: str,
    amount: float,
    attributes: Dict[str, Any],
    images: List[str],
    shipping_options: List[str],
    shipping_price: float,
    threat_metrix: bool,
) -> Result:
    """
    Публикует объявление через /classifieds/.
    Фото уже должны быть загружены и переданы как ссылки в images.
    
    ИСПРАВЛЕНО согласно документации API:
    - amount: float (как в документации)
    - shippingPrice: int (как в документации)
    - shippingOptions: ["DHL_002", "DHL_003"] (как в документации)
    - Добавлены обязательные поля: adAddress, adType, id, handshake
    """
    
    # Нормализация shipping_options согласно документации
    # Документация указывает: ["DHL_002", "DHL_003", "DHL_004"]
    shipping_opts = shipping_options or []
    if not shipping_opts or len(shipping_opts) < 2:
        # Используем коды из документации
        shipping_opts = ["DHL_002", "DHL_003"]
    
    payload: Dict[str, Any] = {
        "account": account,
        "contact": contact,  # Оставляем как передаётся (обычно "Privat")
        "postcode": str(postcode),
        "title": title,
        "description": description,
        "categoryId": str(category_id),
        "priceType": price_type,
        "imprint": imprint or "",
        "amount": float(amount or 0),  # ← FLOAT (как в документации API)
        "attributes": attributes or {},
        "images": images or [],
        "shippingOptions": shipping_opts,  # ← Используем коды из документации
        "shippingPrice": int(shipping_price or 0),  # ← INT (как в документации API)
        "threatMetrix": bool(threat_metrix),
        
        # Добавлены обязательные поля:
        "adAddress": "",
        "adType": "",
        "id": "",
        "handshake": 1,
    }
    status, data = await _add_ad_raw(payload)
    ok = 200 <= int(status) < 300
    return ok, data, status
