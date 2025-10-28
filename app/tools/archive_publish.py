# app/tools/archive_publish.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import time
import secrets
import logging
import random
from typing import Dict, List, Optional, Tuple
from zipfile import ZipFile

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.config import settings
from app.services.accounts import list_accounts as sp_list_accounts

# SuitePro API фасад (без /categories)
from app.services.add import (
    get_category_metadata as sp_get_category_metadata,  # GET /metadata?id=...
    upload_images as sp_upload_images,                  # POST /classifieds/images
    publish_ad as sp_publish_ad,                        # POST /classifieds/
)


log = logging.getLogger("klazfiler.archive.publish")

# ====== вайт-лист категорий (фиксированный) ======
WHITELIST_CATEGORIES: Dict[int, str] = {
    # Auto, Rad & Boot (children)
    223: "Autoteile & Reifen",
    217: "Fahrräder & Zubehör",
    306: "Motorradteile & Zubehör",
    241: "Weiteres Auto, Rad & Boot",

    # Haus & Garten
    84:  "Heimwerken",
    82:  "Lampen & Licht",
    87:  "Weiteres Haus & Garten",

    # Mode & Beauty
    156: "Taschen & Accessoires",
    157: "Uhren & Schmuck",
    155: "Weiteres Mode & Beauty",

    # Elektronik
    172: "Audio & Hifi",
    245: "Foto",
    176: "Haushaltsgeräte",
    279: "Konsolen",
    278: "Notebooks",
    228: "PCs",
    225: "PC-Zubehör & Software",
    285: "Tablets & Reader",
    175: "TV & Video",
    227: "Videospiele",
    168: "Weitere Elektronik",

    # Haustiere
    313: "Zubehör",

    # Familie, Kind & Baby
    25:  "Kinderwagen & Buggys",
    23:  "Spielzeug",

    # Freizeit, Hobby & Nachbarschaft
    249: "Modellbau",
    234: "Sammeln",
    230: "Sport & Camping",
    242: "Weiteres Freizeit, Hobby & Nachbarschaft",

    # Musik, Filme & Bücher
    74:  "Musikinstrumente",
}

# Глобальный кэш для metadata категорий
_METADATA_CACHE: Dict[str, Dict] = {}
_METADATA_CACHE_LOADED = False


def _clean_description(desc: str) -> str:
    """
    Очищает описание от запрещённых элементов:
    1. Убирает даты из "Rechnung" (заменяет на "Rechnung vorhanden")
    2. Убирает упоминания PayPal
    3. Убирает "nur Abholung"
    """
    # 1. Заменяем "Rechnung" с датой на "Rechnung vorhanden"
    desc = re.sub(
        r'Rechnung\s+(vom?|von)\s+\d{1,2}[\./ ]\d{1,2}[\./ ]\d{2,4}',
        'Rechnung vorhanden',
        desc,
        flags=re.IGNORECASE
    )
    desc = re.sub(
        r'Rechnung\s+\d{1,2}[\./ ]\d{1,2}[\./ ]\d{2,4}',
        'Rechnung vorhanden',
        desc,
        flags=re.IGNORECASE
    )
    
    # 2. Убираем упоминания PayPal
    desc = re.sub(
        r'\b(PayPal|Paypal)\s*(Friends?|Familie|Freunde)?\b',
        '',
        desc,
        flags=re.IGNORECASE
    )
    
    # 3. Убираем "nur Abholung"
    desc = re.sub(
        r'\b[Nn]ur\s+[Aa]bholung\b',
        'Abholung möglich',
        desc
    )
    
    # Убираем двойные пробелы и лишние запятые
    desc = re.sub(r'\s+', ' ', desc)
    desc = re.sub(r',\s*,', ',', desc)
    desc = re.sub(r'\s+\.', '.', desc)
    desc = re.sub(r'\s+,', ',', desc)
    
    return desc.strip()

def _whitelist_leaves() -> List[Dict]:
    """Приводим WHITELIST_CATEGORIES к виду [{id,name,path}]"""
    return [{"id": str(cid), "name": name, "path": name} for cid, name in WHITELIST_CATEGORIES.items()]

def _cat_name_by_id(leaves: List[Dict], cid: str) -> str:
    cid = str(cid)
    for c in leaves:
        if str(c.get("id")) == cid:
            return c.get("path") or c.get("name") or ""
    return ""

async def _load_categories_metadata() -> Dict[str, Dict]:
    """
    Загружает metadata для всех категорий из whitelist и кэширует.
    Возвращает dict: {category_id: metadata}
    """
    global _METADATA_CACHE, _METADATA_CACHE_LOADED
    
    # Если уже загружено, возвращаем кэш
    if _METADATA_CACHE_LOADED and _METADATA_CACHE:
        return _METADATA_CACHE
    
    log.info("[METADATA-CACHE] Loading metadata for all whitelist categories...")
    
    for cat_id, cat_name in WHITELIST_CATEGORIES.items():
        try:
            ok, meta_payload, status = await sp_get_category_metadata(str(cat_id))
            if ok and isinstance(meta_payload, dict):
                # Извлекаем только ключевые атрибуты для GPT
                schema = _extract_attribute_schema(meta_payload)
                
                # Фильтруем атрибуты - берём только важные
                key_attrs = []
                for attr in schema:
                    attr_name = attr.get("name", "")
                    # Берём только: art, type, condition, material
                    if any(x in attr_name.lower() for x in [".art", ".type", ".condition", ".material"]):
                        options = attr.get("options", [])
                        if options:
                            # Извлекаем только values
                            if isinstance(options[0], dict):
                                opt_values = [o.get("value") for o in options if o.get("value")]
                            else:
                                opt_values = options
                            
                            key_attrs.append({
                                "name": attr_name,
                                "options": opt_values[:20]  # Ограничиваем до 20 опций
                            })
                
                _METADATA_CACHE[str(cat_id)] = {
                    "id": str(cat_id),
                    "name": cat_name,
                    "attributes": key_attrs
                }
                log.info(f"[METADATA-CACHE] Loaded {cat_id}: {cat_name} ({len(key_attrs)} attrs)")
            else:
                log.warning(f"[METADATA-CACHE] Failed to load {cat_id}: {cat_name}")
        except Exception as e:
            log.warning(f"[METADATA-CACHE] Error loading {cat_id}: {e}")
    
    _METADATA_CACHE_LOADED = True
    log.info(f"[METADATA-CACHE] Loaded {len(_METADATA_CACHE)} categories")
    
    return _METADATA_CACHE

# ====== временное хранилище токенов callback ======
_TOKEN_TTL = 15 * 60
_STORE: Dict[str, Dict] = {}

def _put(data: Dict) -> str:
    tok = secrets.token_urlsafe(12)
    data["_ts"] = time.time()
    _STORE[tok] = data
    return tok

def _get(tok: str) -> Optional[Dict]:
    d = _STORE.get(tok)
    if not d:
        return None
    if time.time() - d.get("_ts", 0) > _TOKEN_TTL:
        _STORE.pop(tok, None)
        return None
    return d

def _cleanup():
    now = time.time()
    for k, v in list(_STORE.items()):
        if now - v.get("_ts", 0) > _TOKEN_TTL:
            _STORE.pop(k, None)

# ====== маленький телеграм-логер ======
class TgStatus:
    def __init__(self, call: CallbackQuery):
        self.call = call
        self.msg = None
        self.lines: List[str] = []

    async def start(self, head: str):
        self.lines = [f"🧰 {head}"]
        self.msg = await self.call.message.answer("\n".join(self.lines))
        return self

    async def add(self, text: str, ok: Optional[bool] = None, icon: Optional[str] = None):
        if icon is None:
            icon = "✅" if ok is True else ("❌" if ok is False else "📝")
        self.lines.append(f"{icon} {text}")
        try:
            await self.msg.edit_text("\n".join(self.lines))
        except Exception:
            self.msg = await self.call.message.answer("\n".join(self.lines))

    async def code(self, title: str, body, max_len: int = 1800):
        if not isinstance(body, str):
            try:
                body = json.dumps(body, ensure_ascii=False, indent=2)
            except Exception:
                body = str(body)
        body = body[:max_len]
        self.lines.append(f"🗂 {title}:\n<code>{body}</code>")
        try:
            await self.msg.edit_text("\n".join(self.lines), parse_mode="HTML")
        except Exception:
            self.msg = await self.call.message.answer("\n".join(self.lines), parse_mode="HTML")


# ====== утилиты чтения архива ======
def _read_info(zip_path: str) -> Tuple[str, str, str, str, str]:
    title = price = url = saved = desc = ""
    try:
        with ZipFile(zip_path, "r") as z:
            name = None
            for zi in z.infolist():
                if not zi.is_dir() and zi.filename.lower().endswith("info.txt"):
                    name = zi.filename
                    break
            if not name:
                return title, price, url, saved, desc
            text = z.read(name).decode("utf-8", errors="ignore")
            m = re.search(r"^Title:\s*(.+)$", text, re.M)
            title = (m.group(1).strip() if m else "")
            m = re.search(r"^Price:\s*(.+)$", text, re.M)
            price = (m.group(1).strip() if m else "")
            m = re.search(r"^URL:\s*(.+)$", text, re.M)
            url = (m.group(1).strip() if m else "")
            m = re.search(r"^Saved at:\s*(.+)$", text, re.M)
            saved = (m.group(1).strip() if m else "")
            m = re.search(r"^Description:\s*\n([\s\S]+)$", text, re.M)
            desc = (m.group(1).strip() if m else "")
    except Exception as e:
        log.warning("read info.txt failed: %s", e)
    return title, price, url, saved, desc

def _extract_images(zip_path: str, max_images: int = 10) -> List[Tuple[str, bytes]]:
    exts = (".jpg", ".jpeg", ".png", ".gif", ".webp")
    out: List[Tuple[str, bytes]] = []
    try:
        with ZipFile(zip_path, "r") as z:
            idx = 1
            for zi in z.infolist():
                if zi.is_dir():
                    continue
                low = zi.filename.lower()
                if not any(low.endswith(e) for e in exts):
                    continue
                try:
                    data = z.read(zi)
                    if data and len(data) >= 10_000:
                        fname = zi.filename.rsplit("/", 1)[-1] or f"image_{idx}.jpg"
                        if not any(fname.lower().endswith(e) for e in exts):
                            fname = f"image_{idx}.jpg"
                        out.append((fname, data))
                        idx += 1
                        if len(out) >= max_images:
                            break
                except Exception:
                    continue
    except Exception as e:
        log.warning("extract images failed: %s", e)
    return out

_PRICE_NUM_RE = re.compile(r"(\d[\d\.\s,]*)")
def _price_from_text(price_str: str) -> Tuple[str, Optional[int]]:
    s = (price_str or "").strip()
    m = _PRICE_NUM_RE.search(s)
    if not m:
        return "PLEASE_CONTACT", None
    raw = m.group(1).replace(".", "").replace(" ", "").replace(",", ".")
    try:
        val = float(raw)
        amt = int(round(val))
        return "SPECIFIED_AMOUNT", max(1, amt)
    except Exception:
        return "PLEASE_CONTACT", None

# набор редких индексов
_SMALL_PLZ = [
    "88131", "78176", "25746", "31812", "99510", "37308", "39615", "54634", "15926",
    "16845", "24837", "25813", "25980", "87727", "83115", "94315", "06721"
]

def _random_postcode() -> str:
    return random.choice(_SMALL_PLZ)

# ====== OpenAI ======

def _pretty(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)

def _oa_chat_json(messages: List[Dict]) -> Dict:
    api_key = (settings.OPENAI_API_KEY or "").strip()
    if not api_key:
        return {}
    base = settings.OPENAI_API_BASE.rstrip("/")
    model = settings.OPENAI_MODEL

    import requests
    log_oa = logging.getLogger("klazfiler.openai")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if getattr(settings, "OPENAI_PROJECT", ""):
        headers["OpenAI-Project"] = settings.OPENAI_PROJECT
    if getattr(settings, "OPENAI_ORG", ""):
        headers["OpenAI-Organization"] = settings.OPENAI_ORG

    body = {"model": model, "response_format": {"type": "json_object"}, "messages": messages}

    # лог запроса (укороченный)
    try:
        log_oa.info("[OpenAI-REQ]\n%s", _pretty(body)[:4000])
    except Exception:
        pass

    try:
        r = requests.post(f"{base}/v1/chat/completions", headers=headers, json=body, timeout=settings.API_TIMEOUT)
        txt = r.text
        try:
            data = r.json()
        except Exception:
            data = {}

        log_oa.info("[OpenAI-RESP] status=%s", r.status_code)
        log_oa.info("[OpenAI-BODY]\n%s", (txt[:4000] if isinstance(txt, str) else _pretty(data)[:4000]))

        r.raise_for_status()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        log_oa.warning("openai failed: %s", e)
        return {}

def _normalize_choice(choice, options):
    if not choice or not options:
        return None
    opts = [str(o) for o in options]
    c = str(choice).strip().lower().replace(" ", "_").replace("-", "_")
    for o in opts:
        if c == o.lower():
            return o
    import difflib
    best = difflib.get_close_matches(c, [o.lower() for o in opts], n=1, cutoff=0.6)
    if best:
        idx = [o.lower() for o in opts].index(best[0])
        return opts[idx]
    return None

def _choose_art_via_gpt(options: list[str], title: str, description: str) -> str | None:
    if not options:
        return None
    # просим ТОЛЬКО одно из options
    sys = (
        "Du hilfst, eine passende Kategorie-Option zu wählen. "
        "Gib die Antwort ausschließlich als JSON-Objekt: {\"art\": \"<wert>\"}. "
        "Der Wert muss GENAU einer der vorgeschlagenen Optionen sein."
    )
    user = {
        "title": title,
        "description": description,
        "options": options
    }
    resp = _oa_chat_json([
        {"role": "system", "content": sys},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)}
    ]) or {}

    # ожидаем {"art": "<one-of-options>"}
    choice = None
    if isinstance(resp, dict):
        if "art" in resp and isinstance(resp["art"], str):
            choice = resp["art"]
        elif "ad" in resp and isinstance(resp["ad"], dict) and isinstance(resp["ad"].get("art"), str):
            choice = resp["ad"]["art"]

    return _normalize_choice(choice, options)

def _extract_attribute_schema(meta_payload: dict) -> list[dict]:
    schema: list[dict] = []

    def walk_to_attributes(node):
        if not isinstance(node, dict):
            return None
        if "attributes" in node and isinstance(node["attributes"], dict):
            arr = node["attributes"].get("attribute")
            if isinstance(arr, list):
                return arr
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
        write = a.get("write")
        required = (write == "required")
        options = []
        sv = a.get("supported-value")
        if isinstance(sv, list):
            for opt in sv:
                if isinstance(opt, dict) and isinstance(opt.get("value"), str):
                    options.append({"value": opt["value"], "label": opt.get("localized-label", opt["value"])})
        if isinstance(name, str) and isinstance(typ, str):
            schema.append({"name": name, "type": typ, "required": required, "options": options})
    return schema

# ====== GPT-заполнение по вайт-листу ======
def _gpt_fill_ad(info: Dict, leaves: List[Dict], categories_with_meta: Dict[str, Dict] = None) -> Dict:
    """
    Просим вернуть JSON:
    {
      title, description,
      priceType: 'SPECIFIED_AMOUNT'|'PLEASE_CONTACT',
      amount: int|null,
      attributes: object,
      category_id: string (из leaves),
      postcode: string (небольшой город Германии),
      reason: string (короткое объяснение выбора)
    }
    """
    if not (settings.OPENAI_API_KEY or "").strip():
        return {}
    # Используем категории с metadata если есть, иначе простой список
    if categories_with_meta:
        cats_compact = list(categories_with_meta.values())
    else:
        cats_compact = [{"id": c["id"], "name": c["name"], "path": c["path"]} for c in leaves][:1000]
    sys = (
        "Ты помощник по заполнению объявлений. Отвечай ТОЛЬКО JSON. "
        "category_id обязан быть одним из переданных id. Не используй категории вне списка. "
        "postcode выбери для небольшого города Германии (например: 88131, 78176, 25980). "
        "Добавь поле reason с кратким объяснением выбора категории (по-немецки). "
        "\n\n"
        "РАБОТА С АТРИБУТАМИ:\n"
        "- Каждая категория имеет поле 'attributes' со списком доступных атрибутов.\n"
        "- Ты ОБЯЗАН заполнить поле 'attributes' в ответе с правильными значениями из списка options.\n"
        "- Формат: {'attribute_name': 'value'}, например: {'elektronik.art': 'netzwerk_modem'}\n"
        "- ВСЕГДА выбирай наиболее подходящее значение для атрибута 'art' или 'type'.\n"
        "\n\n"
        "ВАЖНЫЕ ПРАВИЛА ДЛЯ DESCRIPTION:\n"
        "1. Удали любые упоминания 'Abholung' или 'nur Abholung' - не пиши об этом вообще.\n"
        "2. Удали любые упоминания 'PayPal', 'Paypal', 'PayPal Friends' - не пиши о способах оплаты.\n"
        "3. Удали фразы 'Sichere Bezahlung', 'sicher bezahlen', 'Käuferschutz' - не упоминай безопасность платежей.\n"
        "4. Если упоминается 'Rechnung' с конкретной датой (например 'Rechnung von 18.10.2025' или 'Rechnung vom 15.09.2024'), "
        "замени на простое 'Rechnung vorhanden' или 'Mit Rechnung' БЕЗ указания даты.\n"
        "5. Если в исходном тексте есть 'OVP', 'Originalverpackung', 'NEU' - обязательно сохрани это!\n"
        "6. Сохрани все технические характеристики, модель, состояние товара.\n"
        "7. Описание должно быть коротким (2-4 предложения), но информативным."
    )
    user = json.dumps({"ad": info, "categories": cats_compact}, ensure_ascii=False)
    return _oa_chat_json([
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]) or {}

def _fill_required_attributes(schema: list[dict], base: dict | None = None) -> dict:
    out = dict(base or {})
    for a in schema:
        name = a.get("name")
        if not name or name in out or not a.get("required"):
            continue
        opts = a.get("options") or []
        if opts:
            # берем первый валидный
            if isinstance(opts[0], dict):
                vals = [o.get("value") for o in opts if isinstance(o, dict) and o.get("value")]
                if vals:
                    out[name] = vals[0]
                    continue
            else:
                out[name] = opts[0]
                continue
        typ = (a.get("type") or "").lower()
        if typ in ("boolean", "bool"):
            out[name] = True
        elif typ in ("integer", "number", "int", "float", "double"):
            out[name] = 1
        else:
            out[name] = "generic"
    return out

# ====== выбор аккаунта ======
async def _fetch_accounts_page(cursor: int, limit: int = 12, sort_mode: str = "date") -> Tuple[int, Dict]:
    """
    Получает страницу аккаунтов с сортировкой.
    sort_mode: "date" (по дате) или "ads" (по объявлениям)
    """
    # Определяем параметры сортировки
    if sort_mode == "ads":
        sort_by = "adsCount"
        sort_order = "desc"
    else:
        sort_by = "creationDate"
        sort_order = "asc"
    
    status, data = await sp_list_accounts(
        cursor=cursor, 
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order
    )
    if status != 200 or not isinstance(data, dict):
        return status, {"accounts": [], "hasMore": False, "cursor": 0}
    return status, data

def _kb_accounts(
    tokens: List[Tuple[str, str]], 
    page_token: Optional[str],
    sort_mode: str = "date",
    arch_tok: Optional[str] = None
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора аккаунта с кнопками сортировки.
    sort_mode: "date" или "ads"
    """
    rows: List[List[InlineKeyboardButton]] = []
    for tok, label in tokens:
        rows.append([InlineKeyboardButton(text=label[:32], callback_data=f"apub:pick:{tok}")])
    
    # Навигация
    nav: List[InlineKeyboardButton] = []
    if page_token:
        nav.append(InlineKeyboardButton(text="Вперёд ⟩", callback_data=f"apub:page:{page_token}"))
    if nav:
        rows.append(nav)
    
    # Кнопки сортировки
    if arch_tok:
        sort_row: List[InlineKeyboardButton] = []
        
        if sort_mode == "date":
            sort_row.append(InlineKeyboardButton(
                text="📅 По дате ✓", 
                callback_data="apub:none"
            ))
            sort_row.append(InlineKeyboardButton(
                text="📊 По объявлениям", 
                callback_data=f"apub:sort:ads:{arch_tok}"
            ))
        else:
            sort_row.append(InlineKeyboardButton(
                text="📅 По дате", 
                callback_data=f"apub:sort:date:{arch_tok}"
            ))
            sort_row.append(InlineKeyboardButton(
                text="📊 По объявлениям ✓", 
                callback_data="apub:none"
            ))
        
        rows.append(sort_row)
    
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def start_publish_from_archive(call: CallbackQuery, *, archive_path: str, archive_name: str) -> None:
    await call.answer()
    _cleanup()
    arch_tok = _put({"kind": "archive", "path": archive_path, "name": archive_name, "sort": "date"})

    cursor = 0
    sort_mode = "date"  # По умолчанию сортировка по дате
    status, data = await _fetch_accounts_page(cursor, sort_mode=sort_mode)
    if status != 200:
        await call.message.answer("Не удалось получить список аккаунтов.")
        return

    accs = data.get("accounts") or []
    next_cursor = data.get("nextCursor") or data.get("cursorNext") or data.get("cursor")
    has_more = bool(data.get("hasMore"))
    page_tok = None
    if has_more and isinstance(next_cursor, int):
        page_tok = _put({"kind": "page", "cursor": next_cursor, "arch": arch_tok, "sort": sort_mode})

    btn_tokens: List[Tuple[str, str]] = []
    for a in accs:
        username = a.get("username") or a.get("email")
        if not username:
            continue
        t = _put({"kind": "pick", "username": username, "arch": arch_tok})
        label = f"{username} {'✅' if a.get('loggedIn') else '❌'}{' ✔' if a.get('verified') else ''}"
        btn_tokens.append((t, label))

    kb = _kb_accounts(btn_tokens, page_tok, sort_mode=sort_mode, arch_tok=arch_tok)
    
    sort_label = "по дате (старые → новые)" if sort_mode == "date" else "по объявлениям (больше → меньше)"
    await call.message.answer(
        f"Выбери аккаунт для публикации:\n<b>{archive_name}</b>\n🔄 {sort_label}",
        reply_markup=kb
    )

async def handle_account_page_cb(call: CallbackQuery) -> None:
    await call.answer()
    tok = call.data.split(":", 2)[-1]
    payload = _get(tok)
    if not payload or payload.get("kind") != "page":
        await call.message.answer("Сессия устарела. Начни заново из архива.")
        return
    arch_tok = payload["arch"]
    cursor = int(payload["cursor"])
    sort_mode = payload.get("sort", "date")  # Получаем сортировку из токена

    status, data = await _fetch_accounts_page(cursor, sort_mode=sort_mode)
    if status != 200:
        await call.message.answer("Не удалось получить следующую страницу аккаунтов.")
        return

    accs = data.get("accounts") or []
    next_cursor = data.get("nextCursor") or data.get("cursorNext") or data.get("cursor")
    has_more = bool(data.get("hasMore"))
    page_tok = None
    if has_more and isinstance(next_cursor, int):
        page_tok = _put({"kind": "page", "cursor": next_cursor, "arch": arch_tok, "sort": sort_mode})

    btn_tokens: List[Tuple[str, str]] = []
    for a in accs:
        username = a.get("username") or a.get("email")
        if not username:
            continue
        t = _put({"kind": "pick", "username": username, "arch": arch_tok})
        label = f"{username} {'✅' if a.get('loggedIn') else '❌'}{' ✔' if a.get('verified') else ''}"
        btn_tokens.append((t, label))

    kb = _kb_accounts(btn_tokens, page_tok, sort_mode=sort_mode, arch_tok=arch_tok)
    
    sort_label = "по дате (старые → новые)" if sort_mode == "date" else "по объявлениям (больше → меньше)"
    
    # Обновляем текст и клавиатуру
    arch_payload = _get(arch_tok)
    archive_name = arch_payload.get("name", "товар") if arch_payload else "товар"
    
    try:
        await call.message.edit_text(
            f"Выбери аккаунт для публикации:\n<b>{archive_name}</b>\n🔄 {sort_label}",
            reply_markup=kb
        )
    except Exception as e:
        # Игнорируем ошибку "message is not modified"
        if "message is not modified" not in str(e).lower():
            await call.message.edit_reply_markup(reply_markup=kb)

async def handle_account_sort_cb(call: CallbackQuery) -> None:
    """Обработчик смены сортировки при выборе аккаунта"""
    await call.answer()
    parts = call.data.split(":")
    if len(parts) < 4:
        return
    
    new_sort = parts[2]  # "date" или "ads"
    arch_tok = parts[3]
    
    payload = _get(arch_tok)
    if not payload or payload.get("kind") != "archive":
        await call.message.answer("Сессия устарела. Начни заново из архива.")
        return
    
    # Обновляем сортировку в токене архива
    payload["sort"] = new_sort
    _store[arch_tok] = payload
    
    # Получаем первую страницу с новой сортировкой
    cursor = 0
    status, data = await _fetch_accounts_page(cursor, sort_mode=new_sort)
    if status != 200:
        await call.message.answer("Не удалось получить список аккаунтов.")
        return
    
    accs = data.get("accounts") or []
    next_cursor = data.get("nextCursor") or data.get("cursorNext") or data.get("cursor")
    has_more = bool(data.get("hasMore"))
    page_tok = None
    if has_more and isinstance(next_cursor, int):
        page_tok = _put({"kind": "page", "cursor": next_cursor, "arch": arch_tok, "sort": new_sort})
    
    btn_tokens: List[Tuple[str, str]] = []
    for a in accs:
        username = a.get("username") or a.get("email")
        if not username:
            continue
        t = _put({"kind": "pick", "username": username, "arch": arch_tok})
        label = f"{username} {'✅' if a.get('loggedIn') else '❌'}{' ✔' if a.get('verified') else ''}"
        btn_tokens.append((t, label))
    
    kb = _kb_accounts(btn_tokens, page_tok, sort_mode=new_sort, arch_tok=arch_tok)
    
    sort_label = "по дате (старые → новые)" if new_sort == "date" else "по объявлениям (больше → меньше)"
    archive_name = payload.get("name", "товар")
    
    try:
        await call.message.edit_text(
            f"Выбери аккаунт для публикации:\n<b>{archive_name}</b>\n🔄 {sort_label}",
            reply_markup=kb
        )
    except Exception as e:
        # Игнорируем ошибку "message is not modified"
        if "message is not modified" not in str(e).lower():
            await call.message.edit_reply_markup(reply_markup=kb)

async def handle_account_pick_cb(call: CallbackQuery, state=None) -> None:
    """Обрабатывает выбор аккаунта - запрашивает цену для публикации"""
    if state is None:
        await call.answer("❌ State отсутствует", show_alert=True)
        return
    
    await call.answer()
    
    # Получаем данные из токена
    tok = call.data.split(":", 2)[-1]
    payload = _get(tok)
    if not payload or payload.get("kind") != "pick":
        await call.message.answer("Сессия устарела. Начни заново из архива.")
        return
    
    # Получаем данные архива
    arch = _get(payload["arch"])
    if not arch or arch.get("kind") != "archive":
        await call.message.answer("Сессия архива потеряна. Начни заново.")
        return
    
    username = payload["username"]
    archive_path = arch["path"]
    archive_name = arch["name"]
    
    # Читаем информацию из архива
    title, price_from_archive, url, saved, desc = _read_info(archive_path)
    
    # Генерируем токен публикации
    import hashlib
    publish_token = hashlib.md5(f"{archive_name}{title}{username}".encode()).hexdigest()[:16]
    
    # Сохраняем все данные в FSM state
    await state.update_data(
        username=username,
        archive_path=archive_path,
        archive_name=archive_name,
        title=title,
        price_from_archive=price_from_archive,
        url=url,
        description=desc,
        publish_token=publish_token
    )
    
    # Устанавливаем состояние ожидания цены
    from app.handlers.archive import ArchivePublishSG
    await state.set_state(ArchivePublishSG.waiting_price)
    
    # Запрашиваем цену
    msg = f"💰 <b>Введи цену для публикации</b>\n\n"
    if title:
        msg += f"📦 <b>Товар:</b> {title}\n\n"
    msg += f"Просто напиши число (например: <code>245</code> или <code>199.99</code>)\n\n"
    
    if price_from_archive:
        msg += f"💡 <i>Цена из архива:</i> {price_from_archive}\n\n"
    
    msg += f"<i>Для отмены отправь</i> /cancel"
    
    await call.message.answer(msg)


async def publish_from_archive_with_price(
    call: CallbackQuery,
    *,
    username: str,
    archive_path: str,
    archive_name: str,
    custom_price: float,
    price_type: str = "SPECIFIED_AMOUNT"
) -> None:
    """
    Публикация архива с пользовательской ценой.
    
    Args:
        call: CallbackQuery объект
        username: username аккаунта для публикации
        archive_path: путь к архиву
        archive_name: название архива
        custom_price: пользовательская цена
        price_type: тип цены ("SPECIFIED_AMOUNT" или "PLEASE_CONTACT")
    """
    status_ui = await TgStatus(call).start(f"Публикация с ценой {custom_price}€ под {username}")

    # 1) читаем info.txt
    title0, price0, url0, saved0, desc0 = _read_info(archive_path)
    info = {"title": title0, "price": price0, "description": desc0, "url": url0}
    await status_ui.add("Читаю лот из архива", icon="📦")
    
    # Показываем что прочитали из архива
    archive_info_msg = f"📦 <b>Данные из архива:</b>\n"
    archive_info_msg += f"• <b>Название:</b> {title0 or '(пусто)'}\n"
    archive_info_msg += f"• <b>Цена из архива:</b> {price0 or '(пусто)'}\n"
    if url0:
        archive_info_msg += f"• <b>URL:</b> {url0[:50]}...\n"
    archive_info_msg += f"• <b>Описание:</b> {(desc0[:100] + '...') if len(desc0) > 100 else desc0}\n"
    await call.message.answer(archive_info_msg)
    
    log.info(f"[ARCHIVE-READ] title={title0}, price={price0}, desc_len={len(desc0)}")

    # 2) категории: только вайт-лист
    leaves = _whitelist_leaves()

    # 3) Используем оригинальные данные ИЗ АРХИВА (не переписываем через GPT!)
    title = (title0 or "Kleinanzeige").strip()
    description = (desc0 or url0 or "Privatverkauf.").strip()
    
    # Очищаем описание от запрещённых элементов
    description = _clean_description(description)
    log.info(f"[ARCHIVE-PUBLISH] Using title='{title}', desc_len={len(description)}, price={custom_price}")
    log.info(f"[CLEAN-DESC] Cleaned description")
    
    # GPT используем ТОЛЬКО для выбора категории (если нужно)
    # Загружаем metadata для всех категорий
    categories_with_meta = await _load_categories_metadata() if (settings.OPENAI_API_KEY or "").strip() else {}
    gpt = _gpt_fill_ad(info, leaves, categories_with_meta) if (settings.OPENAI_API_KEY or "").strip() else {}

    def _is_valid_cid(cid: str) -> bool:
        return any(c["id"] == cid for c in leaves)

    category_id = str(gpt.get("category_id") or "").strip()
    reason = (gpt.get("reason") or "").strip()
    picked_by = "gpt"

    # ретрай если не попал
    if not _is_valid_cid(category_id):
        hint = "Wähle die passendste Kategorie AUSSCHLIESSLICH aus der folgenden Whitelist. Gib category_id als Zahl."
        cats_compact = [{"id": c["id"], "name": c["name"]} for c in leaves]
        retry = _oa_chat_json([
            {"role": "system", "content": "Du bist ein Assistent für Kleinanzeigen. Antworte NUR JSON."},
            {"role": "user", "content": json.dumps({"hint": hint, "ad": info, "categories": cats_compact}, ensure_ascii=False)},
        ]) or {}
        category_id = str(retry.get("category_id") or "").strip()
        picked_by = "gpt-retry"

    if not _is_valid_cid(category_id):
        await status_ui.add("Категория от модели не прошла вайт-лист", ok=False)
        await call.message.answer("❌ Не удалось выбрать категорию.")
        return

    cat_name = _cat_name_by_id(leaves, category_id)
    log.info(f"[CATEGORY] selected={category_id} ({cat_name}), picked_by={picked_by}, reason={reason}")
    await status_ui.add(f"Категория: {category_id} — {cat_name}", ok=True)
    
    # Показываем пользователю выбранные данные
    publish_info_msg = f"✅ <b>Будет опубликовано:</b>\n"
    publish_info_msg += f"• <b>Название:</b> {title}\n"
    publish_info_msg += f"• <b>Цена:</b> {custom_price}€ {'VB' if price_type == 'PLEASE_CONTACT' else '(фикс)'}\n"
    publish_info_msg += f"• <b>Категория:</b> {cat_name}\n"
    await call.message.answer(publish_info_msg)

    # 4) фото
    img_files = _extract_images(archive_path)
    img_urls: List[str] = []
    if img_files:
        try:
            ok_up, data_up, st_up = await sp_upload_images(username=username, files=img_files)
        except Exception as e:
            ok_up, data_up = False, {"error": str(e)}

        if ok_up and isinstance(data_up, list):
            img_urls = [str(u) for u in data_up if isinstance(u, str)]
            await status_ui.add(f"Фото загружены: {len(img_urls)} шт", ok=True)
        else:
            await status_ui.add("Фото не загрузились", ok=False)

    # 5) цена - ПОЛЬЗОВАТЕЛЬСКАЯ!
    amt = float(custom_price or 0)
    pt = price_type

    # 6) метаданные и атрибуты
    attrs_final: dict = {}
    try:
        ok_meta, meta_payload, status_meta = await sp_get_category_metadata(str(category_id))
        if ok_meta and isinstance(meta_payload, dict):
            schema = _extract_attribute_schema(meta_payload)
            attrs_from_gpt = gpt.get("attributes") if isinstance(gpt.get("attributes"), dict) else {}
            attrs_final = dict(attrs_from_gpt)

            # versand
            versand_attr = next((a for a in schema if a.get("name", "").endswith(".versand")), None)
            if versand_attr:
                attrs_final[versand_attr["name"]] = "ja"

            # condition
            condition_attr = next((a for a in schema if a.get("name", "").endswith(".condition")), None)
            if condition_attr:
                attrs_final[condition_attr["name"]] = "like_new"

            # art
            art_attr = next((a for a in schema if a.get("name", "").endswith(".art")), None)
            if art_attr:
                art_name = art_attr["name"]
                raw_opts = art_attr.get("options", []) or []
                if raw_opts and isinstance(raw_opts[0], dict):
                    art_options = [o.get("value") for o in raw_opts if isinstance(o, dict) and o.get("value")]
                else:
                    art_options = [str(o) for o in raw_opts]

                if art_name not in attrs_final and art_options:
                    picked = _choose_art_via_gpt(art_options, title, description)
                    if not picked:
                        for fb in ("sonstiges", "weiteres", "andere", "zubehoer"):
                            if fb in art_options:
                                picked = fb
                                break
                        if not picked:
                            picked = art_options[0]
                    attrs_final[art_name] = picked

            # обязательные ENUM
            for a in schema:
                n = a.get("name")
                if not n or n in attrs_final:
                    continue
                opts = a.get("options") or []
                if opts:
                    if isinstance(opts[0], dict):
                        opts = [o.get("value") for o in opts if isinstance(o, dict) and o.get("value")]
                    if opts:
                        attrs_final[n] = opts[0]
    except Exception as e:
        log.info("metadata error: %s", e)

    # 7) PLZ
    postcode = None
    try:
        from app.tools.data_storage import get_account_plz
        postcode = get_account_plz(username)
    except Exception:
        pass
    
    if not postcode:
        postcode = str(gpt.get("postcode") or "").strip() or _random_postcode()
    
    try:
        from app.tools.data_storage import save_account_plz
        save_account_plz(username, postcode)
    except Exception:
        pass

    # 8) публикация
    await status_ui.add(f"Публикую с ценой {amt}€…", icon="🚀")

    res_pub_ok, resp_pub, status_pub = await sp_publish_ad(
        account=username,
        contact="Privat",
        postcode=postcode,
        title=title,
        description=description,
        category_id=category_id,
        price_type=pt,
        imprint="",
        amount=amt,
        attributes=attrs_final,
        images=img_urls,
        shipping_options=["DHL_002"],
        shipping_price=0,
        threat_metrix=True,
    )

    if res_pub_ok:
        await status_ui.add("✅ Объявление опубликовано!", ok=True)
        
        # Обновляем дату публикации
        try:
            from app.tools.data_storage import update_archive_published
            update_archive_published(archive_name, username)
            log.info(f"[ARCHIVE-PUBLISHED] Updated date for {archive_name} -> {username}")
        except Exception as e:
            log.warning(f"Failed to update archive published date: {e}")
    else:
        await status_ui.add(f"❌ Ошибка публикации, HTTP {status_pub}", ok=False)
        await call.message.answer("❌ Публикация не удалась.")
