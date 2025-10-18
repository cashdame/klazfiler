#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import json
import time
import random
import logging
import urllib.parse
from typing import Dict, List, Tuple

import requests
from aiogram import types
from aiogram.dispatcher import FSMContext

logger = logging.getLogger("klazfiler.quickpost")

# ===== Env / Config =====
SUITEPRO_API_KEY = os.getenv("SUITEPRO_API_KEY", "")
SUITEPRO_API_URL = os.getenv("SUITEPRO_API_URL", "https://api.suitepro.to").rstrip("/")
SUITEPRO_CATEGORIES_URL = os.getenv("SUITEPRO_CATEGORIES_URL", f"{SUITEPRO_API_URL}/categories")
SUITEPRO_METADATA_URL = os.getenv("SUITEPRO_METADATA_URL", f"{SUITEPRO_API_URL}/metadata")
SUITEPRO_ACCOUNTS_URL = f"{SUITEPRO_API_URL}/accounts/?search=&loginStatus=all&cursor=0&limit=100&sortBy=creationDate&sortOrder=desc"
SUITEPRO_CLASSIFIEDS_URL = os.getenv("SUITEPRO_CLASSIFIEDS_URL", f"{SUITEPRO_API_URL}/classifieds/")

OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # у тебя так в .env
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "120"))
API_RETRIES = int(os.getenv("API_RETRIES", "3"))

# правила разброса цены как в пасте
MIN_PRICE = int(os.getenv("WARMUP_MIN_PRICE", "10"))
MAX_PRICE = int(os.getenv("WARMUP_MAX_PRICE", "25"))

CATEGORY_STRATEGY = os.getenv("CATEGORY_STRATEGY", "random")  # random|cycle|fixed
FIXED_CATEGORY_ID = os.getenv("CATEGORY_ID", "")

HEADERS_SUITE = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-API-Key": SUITEPRO_API_KEY,
    "User-Agent": "klazfiler/quickpost",
}
HEADERS_OPENAI = {
    "Authorization": f"Bearer {OPENAI_API_KEY}",
    "Content-Type": "application/json",
}

def _req_with_retry(method: str, url: str, **kw) -> requests.Response:
    delay = 1.0
    last_exc = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=API_TIMEOUT, **kw)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < API_RETRIES:
                logger.info(f"[QuickPost] retry {url} ({resp.status_code}) after {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 2, 10)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < API_RETRIES:
                logger.info(f"[QuickPost] retry {url} after error: {e}; sleep {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 2, 10)
            else:
                raise
    # чтобы mypy не ругался
    assert last_exc
    raise last_exc

# ===== SuitePro helpers =====

def fetch_accounts() -> List[Dict]:
    r = _req_with_retry("GET", SUITEPRO_ACCOUNTS_URL, headers=HEADERS_SUITE)
    data = r.json() if r.text else {}
    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    return accounts

def filter_accounts_no_ads(accounts: List[Dict]) -> List[Dict]:
    out = []
    for a in accounts:
        try:
            ads = int(a.get("adsCount", 0))
            is_verified = bool(a.get("verified", False))
            has_error = bool(a.get("hasError", False))
            logged_in = bool(a.get("loggedIn", False))
            if ads == 0 and not has_error and logged_in and is_verified:
                out.append(a)
        except Exception:
            continue
    return out

def fetch_category_map_leaf_only() -> Dict[str, str]:
    r = _req_with_retry("GET", SUITEPRO_CATEGORIES_URL, headers=HEADERS_SUITE)
    payload = r.json()
    category_map: Dict[str, str] = {}

    def is_leaf(node: Dict) -> bool:
        cat_list = node.get("category", None)
        fake_flag = node.get("has-fake-sub-category", {})
        fake_val = fake_flag.get("value") if isinstance(fake_flag, dict) else None
        if isinstance(cat_list, list) and len(cat_list) == 0:
            return (fake_val is None or str(fake_val) == "false")
        if "category" not in node and "id" in node and "localized-name" in node:
            return (fake_val is None or str(fake_val) == "false")
        return False

    def add_if_leaf(node: Dict):
        if not isinstance(node, dict):
            return
        if "id" in node and "localized-name" in node and is_leaf(node):
            cid = node.get("id")
            lname_val = node.get("localized-name", {}).get("value")
            if isinstance(cid, str) and isinstance(lname_val, str) and lname_val.strip():
                category_map[cid] = lname_val

    def walk(node):
        if isinstance(node, dict):
            if "id" in node and "localized-name" in node:
                add_if_leaf(node)
            if "category" in node and isinstance(node["category"], list):
                for sub in node["category"]:
                    walk(sub)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    if not category_map:
        raise RuntimeError("Parsed empty leaf category map from SuitePro.")
    return category_map

def fetch_category_metadata(category_id: str) -> Dict:
    qs = urllib.parse.urlencode({"id": category_id})
    url = f"{SUITEPRO_METADATA_URL}?{qs}"
    r = _req_with_retry("GET", url, headers=HEADERS_SUITE)
    return r.json()

def extract_attribute_schema(meta_payload: Dict) -> List[Dict]:
    schema: List[Dict] = []

    def walk_to_attributes(node):
        if not isinstance(node, dict):
            return None
        if "attributes" in node and isinstance(node["attributes"], dict):
            attrs = node["attributes"].get("attribute")
            if isinstance(attrs, list):
                return attrs
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
        group_name = str(a.get("group-name", "")).lower()
        group_label = str(a.get("group-localized-label", "")).lower()
        if group_name == "badges" or group_label == "verkaufslabels":
            continue

        name = a.get("name")
        typ = a.get("type")
        label = a.get("localized-label")
        write = a.get("write")
        required = (write == "required")
        options = []
        sv = a.get("supported-value")
        if isinstance(sv, list):
            for opt in sv:
                if isinstance(opt, dict) and isinstance(opt.get("value"), str):
                    options.append({
                        "value": opt["value"],
                        "label": opt.get("localized-label", opt["value"])
                    })
        if isinstance(name, str) and isinstance(typ, str):
            schema.append({
                "name": name,
                "type": typ,
                "label": label if isinstance(label, str) else name,
                "required": required,
                "options": options,
            })
    return schema

# ===== OpenAI =====

def oa_chat_json(messages: List[Dict]) -> Dict:
    url = OPENAI_API_BASE.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": OPENAI_MODEL,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    r = _req_with_retry("POST", url, headers=HEADERS_OPENAI, json=payload)
    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception:
        return {}

def build_ad_payload(category_id: str, category_name: str, banned_brands: List[str]) -> Dict:
    banned_json = json.dumps(sorted(set([b for b in banned_brands if b])), ensure_ascii=False)
    messages = [
        {
            "role": "system",
            "content": (
                "Output ONLY valid JSON (no markdown). "
                "Return: {title: string, description: string, price: number, brand: string, category_id: string}. "
                "Price is integer EUR."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Create a Kleinanzeigen ad in category_id '{category_id}' ({category_name}).\n"
                f"- Price must be an integer between {MIN_PRICE} and {MAX_PRICE}.\n"
                "- Use a realistic household item for this category.\n"
                "- Do NOT mention delivery/shipping/pickup.\n"
                f"- Brand must NOT be in this banned list (case-insensitive): {banned_json}\n"
                "- Fields: title, description, price, brand, category_id (must match).\n"
            ),
        },
    ]
    return oa_chat_json(messages) if OPENAI_API_KEY else {}

def build_attributes_payload(ad_obj: Dict, attr_schema: List[Dict]) -> Dict:
    if not attr_schema:
        return {}
    schema_json = json.dumps(attr_schema, ensure_ascii=False)
    ad_json = json.dumps(ad_obj, ensure_ascii=False)
    messages = [
        {
            "role": "system",
            "content": (
                "Output ONLY valid JSON (no markdown). "
                "Return a JSON object that maps EVERY attribute 'name' in SCHEMA to a value. "
                "For ENUMs, value must be exactly one of 'value' options. "
                "Do not add unknown attributes; do not omit."
            ),
        },
        {
            "role": "user",
            "content": f"SCHEMA: {schema_json}\nAD: {ad_json}",
        },
    ]
    return oa_chat_json(messages) if OPENAI_API_KEY else {}

# ===== Telegram handler =====

# точка входа из меню
async def quickpost_entry(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("⚡ Быстрая публикация: запускаю…")

    # 1) получаем аккаунты
    logger.info("[QuickPost] fetch accounts…")
    try:
        accounts = fetch_accounts()
        candidates = filter_accounts_no_ads(accounts)
    except Exception as e:
        logger.info(f"[QuickPost] accounts error: {e}")
        await message.answer("Не смог получить список аккаунтов.")
        return

    if not candidates:
        await message.answer("Нет подходящих аккаунтов: нужно, чтобы был логин, верификация и 0 объявлений.")
        return

    # просто берём один (быстрое разогревочное)
    acc = random.choice(candidates)
    email = acc.get("email") or acc.get("username") or acc.get("id", "unknown")
    logger.info(f"[QuickPost] picked account: {email}")

    # 2) категории
    try:
        cmap = fetch_category_map_leaf_only()
    except Exception as e:
        logger.info(f"[QuickPost] categories error: {e}")
        await message.answer("Не смог получить категории.")
        return

    # выбор категории
    ids = sorted(cmap.keys())
    if CATEGORY_STRATEGY == "fixed" and FIXED_CATEGORY_ID in cmap:
        cat_id = FIXED_CATEGORY_ID
    else:
        cat_id = random.choice(ids)
    cat_name = cmap.get(cat_id, "Kategorie")

    # 3) метаданные и схема атрибутов
    meta = fetch_category_metadata(cat_id)
    schema = extract_attribute_schema(meta)

    # 4) запрет брендов (простая память на время процесса)
    banned = []

    # 5) генерим объявление
    ad_obj = build_ad_payload(cat_id, cat_name, banned) or {}
    # фолбэк если OAI нет/упал
    if not ad_obj or ad_obj.get("category_id") != cat_id:
        price = random.randint(MIN_PRICE, MAX_PRICE)
        ad_obj = {
            "title": f"{cat_name} – guter Zustand",
            "description": "Privatverkauf. Abholung nach Absprache. Keine Garantie.",
            "price": price,
            "brand": "Generic",
            "category_id": cat_id,
        }

    # 6) атрибуты, если есть схема
    attrs = build_attributes_payload(ad_obj, schema) if schema else {}

    # 7) собираем payload для SuitePro
    payload = {
        "categoryId": ad_obj["category_id"],
        "title": ad_obj["title"],
        "description": ad_obj["description"],
        "price": int(ad_obj["price"]),
        "attributes": attrs if isinstance(attrs, dict) else {},
        # картинки не загружаем в этой «быстрой» версии
        "images": [],
    }

    # 8) POST /classifieds/
    try:
        logger.info(f"[QuickPost] POST {SUITEPRO_CLASSIFIEDS_URL} json={json.dumps(payload, ensure_ascii=False)[:600]}")
        r = _req_with_retry("POST", SUITEPRO_CLASSIFIEDS_URL, headers=HEADERS_SUITE, json=payload)
        resp = r.json() if r.text else {}
        logger.info(f"[QuickPost] created: status={r.status_code} resp={str(resp)[:500]}")
    except Exception as e:
        logger.info(f"[QuickPost] create error: {e}")
        await message.answer("Не удалось опубликовать объявление (см. логи).")
        return

    await message.answer(
        "Готово. Опубликовал быстрый пост:\n"
        f"• Аккаунт: {email}\n"
        f"• Категория: {cat_name}\n"
        f"• Заголовок: {ad_obj['title']}\n"
        f"• Цена: {ad_obj['price']} €"
    )
