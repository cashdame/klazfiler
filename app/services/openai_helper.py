# app/services/openai_helper.py
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

import requests

from app.config import settings

log = logging.getLogger("klazfiler.openai")

def _gpt_fill_ad(ad_info: dict, categories: List[dict] = None) -> dict:
    """
    Заполняет объявление с помощью GPT.
    Возвращает dict с полями: title, description, category_id, postcode, priceType, amount, attributes
    """
    if not (getattr(settings, "OPENAI_API_KEY", "") or "").strip():
        return {}

    api_key = settings.OPENAI_API_KEY
    base_url = (settings.OPENAI_API_BASE or "https://api.openai.com").rstrip("/")
    model = settings.OPENAI_MODEL or "gpt-4o-mini"

    # Подготовка payload
    payload = {
        "ad": {
            "title": ad_info.get("title", ""),
            "price": ad_info.get("price", ""),
            "description": ad_info.get("description", ""),
            "url": ad_info.get("url", ""),
        }
    }

    if categories:
        payload["categories"] = [
            {
                "id": cat.get("id"),
                "name": cat.get("name") or cat.get("localized-name", {}).get("value", ""),
                "path": cat.get("path", "")
            }
            for cat in categories[:30]  # ограничиваем чтобы не превысить лимиты токенов
        ]

    messages = [
        {
            "role": "system",
            "content": "Ты помощник по заполнению объявлений. Ответ только JSON. category_id должен быть из переданного списка категорий. postcode выбери для небольшого города Германии, можно из примеров: 88131, 78176, 25980."
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False)
        }
    ]

    api_payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": messages,
        "temperature": 0.3,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Добавляем опциональные заголовки организации и проекта
    if getattr(settings, "OPENAI_ORG", ""):
        headers["OpenAI-Organization"] = settings.OPENAI_ORG
    if getattr(settings, "OPENAI_PROJECT", ""):
        headers["OpenAI-Project"] = settings.OPENAI_PROJECT

    url = f"{base_url}/v1/chat/completions"
    
    log.info("[OpenAI-URL] %s", url)
    log.info("[OpenAI-REQ] %s", json.dumps(api_payload, ensure_ascii=False, indent=2))

    try:
        response = requests.post(
            url,
            headers=headers,
            json=api_payload,
            timeout=settings.API_TIMEOUT
        )
        response.raise_for_status()
        
        data = response.json()
        log.info("[OpenAI-RESP] status=%s", response.status_code)
        log.info("[OpenAI-BODY] %s", json.dumps(data, ensure_ascii=False, indent=2))
        
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)
        
        # Извлекаем данные из структуры response -> ad
        if "ad" in result:
            return result["ad"]
        else:
            return result
            
    except requests.exceptions.RequestException as e:
        log.error("[OpenAI] Request error: %s", e)
        return {}
    except json.JSONDecodeError as e:
        log.error("[OpenAI] JSON decode error: %s", e)
        return {}
    except Exception as e:
        log.error("[OpenAI] Unexpected error: %s", e)
        return {}