#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import time
import random
import logging
import urllib.parse
from typing import Dict, List, Any

import requests
from aiogram import types
from app.config import settings
from app.services.accounts import list_accounts as sp_list_accounts

logger = logging.getLogger("klazfiler.quickpost")

# ==== Настройки ====
SUITEPRO_API_KEY = settings.SUITEPRO_API_KEY
SUITEPRO_CATEGORIES_URL = settings.categories_url
SUITEPRO_METADATA_URL = settings.metadata_url
SUITEPRO_CLASSIFIEDS_URL = settings.classifieds_url

OPENAI_API_BASE = settings.OPENAI_API_BASE.rstrip("/")
OPENAI_API_KEY = (settings.OPENAI_API_KEY or "").strip()
OPENAI_MODEL = settings.OPENAI_MODEL
OPENAI_ORG = (getattr(settings, "OPENAI_ORG", "") or "").strip()
OPENAI_PROJECT = (getattr(settings, "OPENAI_PROJECT", "") or "").strip()

API_TIMEOUT = settings.API_TIMEOUT
API_RETRIES = getattr(settings, "API_RETRIES", 3)

MIN_PRICE = int(getattr(settings, "WARMUP_MIN_PRICE", 10))
MAX_PRICE = int(getattr(settings, "WARMUP_MAX_PRICE", 25))
CATEGORY_STRATEGY = getattr(settings, "CATEGORY_STRATEGY", "random")  # random|fixed
FIXED_CATEGORY_ID = getattr(settings, "CATEGORY_ID", "")

HEADERS_SUITE = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-API-Key": SUITEPRO_API_KEY,
    "User-Agent": "klazfiler/quickpost",
}

# Набор валидных немецких индексов (минимальный пул)
_VALID_DE_PLZ = [
    "10115","10243","10405","10557",  # Berlin
    "20095","20354","22041","22767",  # Hamburg
    "50667","50823","50937","51103",  # Köln
    "60311","60439","60594","65929",  # Frankfurt
    "70173","70469","70565","70619",  # Stuttgart
    "80331","80636","80995","81667",  # München
    "01067","01159","01307","01445",  # Dresden/Radebeul
    "04109","04229","04357","04416",  # Leipzig/Markkleeberg
    "28195","28217","28359","28755",  # Bremen
    "39104","39120","39126","39130",  # Magdeburg
    "99084","99089","99092","99099",  # Erfurt
    "90402","90459","90471","90491",  # Nürnberg
    "23552","23562","23566","23570",  # Lübeck
    "24103","24114","24116","24149",  # Kiel
    "34117","34125","34128","34134",  # Kassel
]
def random_german_postcode() -> str:
    return random.choice(_VALID_DE_PLZ)

# ==== HTTP с ретраями и логом тела ошибки ====
def _req_with_retry(method: str, url: str, **kw) -> requests.Response:
    delay = 1.0
    for attempt in range(1, API_RETRIES + 1):
        try:
            r = requests.request(method, url, timeout=API_TIMEOUT, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < API_RETRIES:
                try:
                    logger.info(f"[QuickPost] error body: {r.text[:1000]}")
                except Exception:
                    pass
                logger.info(f"[QuickPost] retry {url} ({r.status_code}) after {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 2, 10)
                continue
            if r.status_code >= 400:
                try:
                    logger.info(f"[QuickPost] error body: {r.text[:1000]}")
                except Exception:
                    pass
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt < API_RETRIES:
                logger.info(f"[QuickPost] retry {url} after error: {e}; sleep {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 2, 10)
            else:
                raise

# ==== Accounts via services ====
async def _fetch_all_accounts(max_pages: int = 5, page_limit: int = 50) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    cursor = 0
    for _ in range(max_pages):
        status, data = await sp_list_accounts(cursor=cursor, limit=page_limit)
        if status != 200 or not isinstance(data, dict):
            logger.warning(f"[QuickPost] accounts status={status} data={str(data)[:300]}")
            break
        items = data.get("accounts") or data.get("items") or data.get("data") or []
        if isinstance(items, list):
            out.extend([x for x in items if isinstance(x, dict)])
        next_cursor = data.get("nextCursor") or data.get("cursorNext") or data.get("cursor")
        if not next_cursor or next_cursor == 0:
            break
        cursor = next_cursor
    return out

def _filter_accounts_no_ads(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ok: List[Dict[str, Any]] = []
    for a in accounts:
        try:
            if (
                int(a.get("adsCount", 0)) == 0
                and not bool(a.get("hasError"))
                and bool(a.get("loggedIn"))
                and bool(a.get("verified"))
            ):
                ok.append(a)
        except Exception:
            continue
    return ok

# ==== Categories / metadata ====
def fetch_category_map_leaf_only() -> Dict[str, str]:
    r = _req_with_retry("GET", SUITEPRO_CATEGORIES_URL, headers=HEADERS_SUITE)
    payload = r.json()
    category_map: Dict[str, str] = {}

    def is_leaf(node: Dict) -> bool:
        cat_list = node.get("category", None)
        fake_flag = node.get("has-fake-sub-category", {})
        fake_val = fake_flag.get("value") if isinstance(fake_flag, dict) else None
        if isinstance(cat_list, list) and len(cat_list) == 0:
            return (fake_val is None or str(fake_val).lower() == "false")
        if "category" not in node and "id" in node and "localized-name" in node:
            return (fake_val is None or str(fake_val).lower() == "false")
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
        raise RuntimeError("Parsed empty category map.")
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
        if str(a.get("group-name", "")).lower() == "badges":
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

# ==== OpenAI ====
def oa_chat_json(messages: List[Dict]) -> Dict:
    if not OPENAI_API_KEY:
        return {}

    url = f"{OPENAI_API_BASE}/v1/chat/completions"
    payload = {
        "model": OPENAI_MODEL,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    if OPENAI_PROJECT:
        headers["OpenAI-Project"] = OPENAI_PROJECT
    if OPENAI_ORG:
        headers["OpenAI-Organization"] = OPENAI_ORG

    try:
        r = _req_with_retry("POST", url, headers=headers, json=payload)
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) == 401:
            logger.info("[QuickPost] OpenAI 401 Unauthorized — fallback to basic template")
            return {}
        raise
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
                "- Fields: title, description, price, brand, category_id (must match)."
            ),
        },
    ]
    return oa_chat_json(messages)

def _first_valid_option(options: List[Dict[str, Any]]) -> Any:
    for opt in options or []:
        v = opt.get("value")
        if v not in (None, "", "null"):
            return v
    return None

def fill_required_attributes(schema: List[Dict], base: Dict | None = None) -> Dict:
    attrs = dict(base or {})
    for a in schema or []:
        name = a.get("name")
        if not name or name in attrs:
            continue
        if not a.get("required"):
            continue
        typ = (a.get("type") or "").lower()
        opts = a.get("options") or []
        if opts:
            val = _first_valid_option(opts)
            if val is not None:
                attrs[name] = val
                continue
        if typ in ("boolean", "bool"):
            attrs[name] = True
        elif typ in ("integer", "number", "int", "float", "double"):
            attrs[name] = 1
        else:
            attrs[name] = "generic"
    return attrs

def build_attributes_payload(ad_obj: Dict, attr_schema: List[Dict]) -> Dict:
    if not attr_schema or not OPENAI_API_KEY:
        return {}
    schema_json = json.dumps(attr_schema, ensure_ascii=False)
    ad_json = json.dumps(ad_obj, ensure_ascii=False)
    messages = [
        {
            "role": "system",
            "content": (
                "Output ONLY valid JSON (no markdown). "
                "Return JSON object that maps EVERY attribute 'name' in SCHEMA to a value."
            ),
        },
        {
            "role": "user",
            "content": f"SCHEMA: {schema_json}\nAD: {ad_json}",
        },
    ]
    return oa_chat_json(messages)

# ==== Telegram handler ====
async def quickpost_entry(message: types.Message):
    await message.answer("⚡ Быстрая публикация: запускаю…")

    # 1) Аккаунт
    try:
        accounts = await _fetch_all_accounts()
        candidates = _filter_accounts_no_ads(accounts)
    except Exception as e:
        logger.error(f"[QuickPost] accounts error: {e}")
        await message.answer("Не смог получить список аккаунтов.")
        return
    if not candidates:
        await message.answer("Нет подходящих аккаунтов: нужен логин, верификация и 0 объявлений.")
        return
    acc = random.choice(candidates)
    email = acc.get("email") or acc.get("username") or acc.get("id", "unknown")

    # 2) Категория
    try:
        cmap = fetch_category_map_leaf_only()
    except Exception as e:
        logger.error(f"[QuickPost] categories error: {e}")
        await message.answer("Не смог получить категории.")
        return
    ids = sorted(cmap.keys())
    cat_id = FIXED_CATEGORY_ID if CATEGORY_STRATEGY == "fixed" and FIXED_CATEGORY_ID in cmap else random.choice(ids)
    cat_name = cmap.get(cat_id, "Kategorie")

    # 3) Метаданные -> схема атрибутов
    meta = fetch_category_metadata(cat_id)
    schema = extract_attribute_schema(meta)

    # 4) Объявление + атрибуты
    banned: List[str] = []
    ad_obj = build_ad_payload(cat_id, cat_name, banned) or {}
    if ad_obj.get("category_id") != cat_id or not ad_obj.get("title") or not ad_obj.get("description"):
        ad_obj = {
            "title": f"{cat_name} – guter Zustand",
            "description": "Privatverkauf. Abholung nach Absprache. Keine Garantie.",
            "price": random.randint(MIN_PRICE, MAX_PRICE),
            "brand": "Generic",
            "category_id": cat_id,
        }

    attrs_ai = build_attributes_payload(ad_obj, schema) if schema else {}
    if not isinstance(attrs_ai, dict):
        attrs_ai = {}
    attrs = fill_required_attributes(schema, attrs_ai)

    # 5) Формируем payload как в твоём рабочем скрипте
    payload = {
        "title": ad_obj["title"],
        "description": ad_obj["description"],
        "categoryId": str(ad_obj["category_id"]),
        "adAddress": "",
        "priceType": "SPECIFIED_AMOUNT",
        "postcode": random_german_postcode(),
        "contact": "",
        "imprint": "",
        "amount": int(ad_obj["price"]),        # ВАЖНО: amount, не price
        "adType": "",
        "attributes": attrs,
        "shippingOptions": ["DHL_001", "HERMES_001"],
        "images": [],
        "id": "",
        "handshake": 1,
        "threatmetrix": True,
        "account": email,                      # ВАЖНО: account = email
    }

    # 6) Публикация
    try:
        logger.info(f"[QuickPost] POST {SUITEPRO_CLASSIFIEDS_URL}")
        r = _req_with_retry("POST", SUITEPRO_CLASSIFIEDS_URL, headers=HEADERS_SUITE, json=payload)
        resp = r.json() if r.text else {}
        logger.info(f"[QuickPost] created: status={r.status_code} resp={str(resp)[:500]}")
    except Exception as e:
        logger.error(f"[QuickPost] create error: {e}")
        await message.answer("Не удалось опубликовать объявление (см. логи).")
        return

    await message.answer(
        "✅ Опубликовал быстрый пост:\n"
        f"• Аккаунт: {email}\n"
        f"• Категория: {cat_name}\n"
        f"• Заголовок: {ad_obj['title']}\n"
        f"• Цена: {ad_obj['price']} €"
    )
