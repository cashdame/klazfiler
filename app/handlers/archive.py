# app/handlers/archive.py
# -*- coding: utf-8 -*-
import os
import re
import json
import logging
from contextlib import suppress
from zipfile import ZipFile
from typing import List, Tuple, Optional, Any

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from app.config import settings
from app.services.api.classifieds import upload_images, add_ad, get_category_metadata
from app.services.api.categories import list_categories as svc_list_categories
from app.services.accounts import list_accounts as svc_list_accounts

router = Router()
log = logging.getLogger("klazfiler.archive")

PAGE_SIZE = 10
ACC_PAGE = 12

# -------------------- Локальные «заглушки» вместо app.ai.* --------------------

def extract_from_info_ai_fallback(title: str, desc: str, price_str: str) -> dict:
    """
    Вместо AI: берём как есть. Число из строки цены — в amount.
    """
    price_type, amount = _parse_amount(price_str)
    return {
        "title": title.strip() or "Anzeige",
        "description": (desc or "").strip() or "Privatverkauf.",
        "amount": amount,
    }

def suggest_postcode_fallback(title: str, desc: str) -> str:
    """
    Простая подсказка PLZ. Если нет — вернём берлинский индекс.
    """
    m = re.search(r"\b(\d{5})\b", f"{title} {desc}")
    return m.group(1) if m else "10115"

def _meta_attributes_list(meta: Any) -> List[dict]:
    """
    Достаём список атрибутов вида [{"name":..., "type":..., "required":bool, "options":[{"value":...},...]}, ...]
    из ответа get_category_metadata.
    """
    out: List[dict] = []

    def walk_to_attributes(node: Any) -> Optional[List[dict]]:
        if isinstance(node, dict):
            if "attributes" in node and isinstance(node["attributes"], dict):
                arr = node["attributes"].get("attribute")
                if isinstance(arr, list):
                    return arr
            for v in node.values():
                res = walk_to_attributes(v)
                if res is not None:
                    return res
        elif isinstance(node, list):
            for item in node:
                res = walk_to_attributes(item)
                if res is not None:
                    return res
        return None

    attrs = walk_to_attributes(meta) or []
    for a in attrs:
        if not isinstance(a, dict):
            continue
        name = a.get("name")
        typ = a.get("type")
        req = (a.get("write") == "required")
        opts_list = []
        sv = a.get("supported-value")
        if isinstance(sv, list):
            for o in sv:
                if isinstance(o, dict) and isinstance(o.get("value"), str):
                    opts_list.append({"value": o["value"]})
        if isinstance(name, str) and isinstance(typ, str):
            out.append({"name": name, "type": typ, "required": bool(req), "options": opts_list})
    return out

def _first_valid_option(options: List[dict]) -> Optional[str]:
    for o in options or []:
        v = o.get("value")
        if v not in (None, "", "null"):
            return v
    return None

def suggest_attrs_for_category_fallback(meta: Any, title: str, desc: str) -> dict:
    """
    Вместо AI: для каждого required-атрибута выбираем первый допустимый option,
    иначе ставим разумный дефолт по типу.
    """
    attrs_schema = _meta_attributes_list(meta)
    out = {}
    for a in attrs_schema:
        if not a.get("required"):
            continue
        name = a["name"]
        opts = a.get("options") or []
        if opts:
            v = _first_valid_option(opts)
            if v is not None:
                out[name] = v
                continue
        typ = (a.get("type") or "").lower()
        if typ in ("boolean", "bool"):
            out[name] = True
        elif typ in ("integer", "number", "int", "float", "double"):
            out[name] = 1
        else:
            out[name] = "generic"
    return out

def ensure_required_attrs_fallback(meta: Any, attrs: dict, title: str, desc: str, category_name: str) -> dict:
    """
    Добиваем все обязательные атрибуты, которых нет в attrs.
    """
    base = dict(attrs or {})
    schema = _meta_attributes_list(meta)
    for a in schema:
        if not a.get("required"):
            continue
        name = a["name"]
        if name in base:
            continue
        opts = a.get("options") or []
        if opts:
            v = _first_valid_option(opts)
            if v is not None:
                base[name] = v
                continue
        typ = (a.get("type") or "").lower()
        if typ in ("boolean", "bool"):
            base[name] = True
        elif typ in ("integer", "number", "int", "float", "double"):
            base[name] = 1
        else:
            base[name] = "generic"
    return base

def validate_attribute_map_fallback(meta: Any, attrs: dict, category_name: str) -> Tuple[dict, str]:
    """
    Простейшая «валидация»: если значение не подходит по option — заменим на первый допустимый.
    Возвращаем (attrs, report_text).
    """
    schema = _meta_attributes_list(meta)
    by_name = {s["name"]: s for s in schema}
    fixed = dict(attrs or {})
    changes = []
    for k, v in list(fixed.items()):
        sc = by_name.get(k)
        if not sc:
            continue
        opts = sc.get("options") or []
        if opts:
            valid_values = {o.get("value") for o in opts}
            if str(v) not in valid_values:
                new_v = _first_valid_option(opts)
                if new_v is not None:
                    changes.append(f"{k}: {v} -> {new_v}")
                    fixed[k] = new_v
    report = ("Исправлены атрибуты: " + ", ".join(changes)) if changes else ""
    return fixed, report

# ------------------------------------------------------------------------------

def _archive_dir() -> str:
    d = (getattr(settings, "ARCHIVE_DIR", "") or "").strip()
    return d or os.getcwd()

def _list_archives() -> List[str]:
    d = _archive_dir()
    files = []
    with suppress(Exception):
        for name in os.listdir(d):
            if name.lower().endswith(".zip"):
                files.append(name)
    files.sort(key=lambda n: os.path.getmtime(os.path.join(d, n)), reverse=True)
    return files

def _read_info(zip_path: str) -> Tuple[str, str, str, str, str]:
    title = price = url = saved = desc = ""
    try:
        with ZipFile(zip_path, "r") as z:
            candidate = None
            for zi in z.infolist():
                if zi.is_dir():
                    continue
                if zi.filename.lower().endswith("info.txt"):
                    candidate = zi
                    break
            if not candidate:
                return title, price, url, saved, desc
            text = z.read(candidate).decode("utf-8", errors="ignore")
            m_title = re.search(r"^Title:\s*(.+)$", text, re.MULTILINE)
            m_price = re.search(r"^Price:\s*(.+)$", text, re.MULTILINE)
            m_url = re.search(r"^URL:\s*(.+)$", text, re.MULTILINE)
            m_saved = re.search(r"^Saved at:\s*(.+)$", text, re.MULTILINE)
            m_desc = re.search(r"^Description:\s*\n([\s\S]+)$", text, re.MULTILINE)
            title = (m_title.group(1).strip() if m_title else "")
            price = (m_price.group(1).strip() if m_price else "")
            url = (m_url.group(1).strip() if m_url else "")
            saved = (m_saved.group(1).strip() if m_saved else "")
            desc = (m_desc.group(1).strip() if m_desc else "")
    except Exception:
        pass
    return title, price, url, saved, desc

def _parse_amount(price_str: str) -> Tuple[str, int]:
    s = (price_str or "").strip().lower()
    m = re.search(r"(\d+(?:[.,]\d+)?)", s)
    amount = 0
    if m:
        val = m.group(1).replace(",", ".")
        with suppress(Exception):
            amount = int(float(val))
    return "SPECIFIED_AMOUNT", amount

def _kb_archive_page(page: int) -> InlineKeyboardMarkup:
    items = _list_archives()
    total = len(items)
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    slice_items = items[start:end]
    rows = []
    for name in slice_items:
        rows.append([InlineKeyboardButton(text=name[:60], callback_data=f"arch:open:{name}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⟨ Назад", callback_data=f"arch:page:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton(text="Вперёд ⟩", callback_data=f"arch:page:{page+1}"))
    rows.append(nav or [InlineKeyboardButton(text="Закрыть", callback_data="arch:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_archive_item(name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬇️ Скачать", callback_data=f"arch:send:{name}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"arch:delete:{name}")],
        [InlineKeyboardButton(text="📤 Опубликовать", callback_data=f"arch:publish:{name}")],
        [InlineKeyboardButton(text="← Назад к списку", callback_data="arch:page:0")],
    ])

def _kb_accounts_page(accounts: List[dict], page: int) -> InlineKeyboardMarkup:
    start = page * ACC_PAGE
    end = start + ACC_PAGE
    slice_items = accounts[start:end]
    rows = []
    for a in slice_items:
        email = a.get("email") or a.get("username") or a.get("id")
        txt = f"{email} {'✅' if a.get('verified') else '❌'}"
        rows.append([InlineKeyboardButton(text=txt[:60], callback_data=f"arch:pub:sel:{email}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⟨ Назад", callback_data=f"arch:pub:page:{page-1}"))
    if end < len(accounts):
        nav.append(InlineKeyboardButton(text="Вперёд ⟩", callback_data=f"arch:pub:page:{page+1}"))
    rows.append(nav or [InlineKeyboardButton(text="Отмена", callback_data="arch:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

class PublishStates(StatesGroup):
    waiting_username = State()
    waiting_postcode = State()
    waiting_category = State()

@router.message(F.text.contains("Архив"))
async def archive_open(message: Message) -> None:
    kb = _kb_archive_page(0)
    await message.answer(f"Найдено архивов: {len(_list_archives())}", reply_markup=kb)

@router.callback_query(F.data.startswith("arch:page:"))
async def archive_page(call: CallbackQuery) -> None:
    try:
        page = int(call.data.split(":")[-1])
    except Exception:
        page = 0
    kb = _kb_archive_page(max(0, page))
    with suppress(Exception):
        await call.message.edit_text(f"Найдено архивов: {len(_list_archives())}", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("arch:open:"))
async def archive_open_item(call: CallbackQuery) -> None:
    name = call.data.split(":", 2)[-1]
    path = os.path.join(_archive_dir(), name)
    title, price, url, saved, desc = _read_info(path)
    info = (
        f"<b>{name}</b>\n"
        f"Title: {title or '—'}\n"
        f"Price: {price or '—'}\n"
        f"URL: {url or '—'}\n"
        f"Saved at: {saved or '—'}\n\n"
        f"{(desc[:1500] + '…') if len(desc) > 1500 else desc}"
    )
    kb = _kb_archive_item(name)
    with suppress(Exception):
        await call.message.edit_text(info, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("arch:send:"))
async def archive_send(call: CallbackQuery) -> None:
    name = call.data.split(":", 2)[-1]
    path = os.path.join(_archive_dir(), name)
    with suppress(Exception):
        await call.message.answer_document(FSInputFile(path))
    await call.answer()

@router.callback_query(F.data.startswith("arch:delete:"))
async def archive_delete(call: CallbackQuery) -> None:
    name = call.data.split(":", 2)[-1]
    path = os.path.join(_archive_dir(), name)
    try:
        os.remove(path)
        kb = _kb_archive_page(0)
        with suppress(Exception):
            await call.message.edit_text(f"Найдено архивов: {len(_list_archives())}", reply_markup=kb)
    except Exception:
        await call.answer("Не удалось удалить", show_alert=True)

@router.callback_query(F.data == "arch:close")
async def archive_close(call: CallbackQuery) -> None:
    with suppress(Exception):
        await call.message.delete()
    await call.answer()

# -------------------- Публикация --------------------

@router.callback_query(F.data.startswith("arch:publish:"))
async def publish_start(call: CallbackQuery, state: FSMContext) -> None:
    name = call.data.split(":", 2)[-1]
    await state.update_data(pub_name=name)
    status, data = await svc_list_accounts(cursor=0, limit=100)
    accounts = []
    if status == 200 and isinstance(data, dict):
        items = data.get("accounts") or data.get("items") or data.get("data") or []
        if isinstance(items, list):
            accounts = [x for x in items if isinstance(x, dict)]
    if not accounts:
        await call.answer("Нет аккаунтов", show_alert=True)
        return
    await state.set_state(PublishStates.waiting_username)
    kb = _kb_accounts_page(accounts, 0)
    with suppress(Exception):
        await call.message.edit_text("Выбери аккаунт для публикации:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("arch:pub:page:"))
async def publish_accounts_page(call: CallbackQuery, state: FSMContext) -> None:
    try:
        page = int(call.data.split(":")[-1])
    except Exception:
        page = 0
    status, data = await svc_list_accounts(cursor=page * 100, limit=100)
    accounts = []
    if status == 200 and isinstance(data, dict):
        items = data.get("accounts") or data.get("items") or data.get("data") or []
        if isinstance(items, list):
            accounts = [x for x in items if isinstance(x, dict)]
    kb = _kb_accounts_page(accounts, 0)
    with suppress(Exception):
        await call.message.edit_text("Выбери аккаунт для публикации:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("arch:pub:sel:"))
async def publish_account_selected(call: CallbackQuery, state: FSMContext) -> None:
    username = call.data.split(":", 3)[-1]
    await state.update_data(pub_username=username)

    data = await state.get_data()
    name = data.get("pub_name")
    if not name:
        await call.answer()
        return
    path = os.path.join(_archive_dir(), name)

    # читаем info.txt
    title, price_str, url, saved, desc = _read_info(path)

    # вместо AI: аккуратно достанем заголовок/описание/цену
    ai_res = extract_from_info_ai_fallback(title, desc, price_str)
    await state.update_data(
        pub_ai_title=ai_res.get("title", "").strip(),
        pub_ai_desc=ai_res.get("description", "").strip(),
        pub_ai_amount=ai_res.get("amount"),
    )

    # шаг 2: индекс
    suggestion = suggest_postcode_fallback(title, desc)
    hint = f"\nПодсказка: {suggestion}" if suggestion else ""
    await state.set_state(PublishStates.waiting_postcode)
    with suppress(Exception):
        await call.message.edit_text("Введи почтовый индекс (PLZ), например 10115." + hint)
    await call.answer()

@router.message(PublishStates.waiting_postcode)
async def publish_postcode(message: Message, state: FSMContext) -> None:
    plz = (message.text or "").strip()
    if not re.fullmatch(r"\d{5}", plz):
        await message.answer("Нужен индекс в формате 5 цифр, например 10115.")
        return
    await state.update_data(pub_postcode=plz)

    # подсказка по категории (без AI — покажем, если ранее сохраняли)
    data = await state.get_data()
    cat_hint = ""
    sugg = data.get("pub_suggest_cat")
    if sugg:
        cat_hint = f"\nРекомендуемая категория: {sugg}"
    await state.set_state(PublishStates.waiting_category)
    await message.answer("Укажи categoryId (например 240)." + cat_hint)

@router.message(PublishStates.waiting_category)
async def publish_category(message: Message, state: FSMContext) -> None:
    category_id = (message.text or "").strip()
    if not category_id:
        await message.answer("Укажи categoryId")
        return

    status_meta, meta = await get_category_metadata(category_id)
    if status_meta != 200:
        await message.answer("Категория не найдена. Укажи другой categoryId.")
        return

    data = await state.get_data()
    name = data.get("pub_name")
    username = data.get("pub_username")
    postcode = data.get("pub_postcode")
    ai_title = data.get("pub_ai_title")
    ai_desc = data.get("pub_ai_desc")
    ai_amount = data.get("pub_ai_amount")

    if not name or not username:
        await message.answer("Сессия публикации потеряна. Начни заново.")
        return

    path = os.path.join(_archive_dir(), name)
    title, price_str, url, saved, desc = _read_info(path)

    # цена
    price_type = "SPECIFIED_AMOUNT"
    amount = ai_amount if isinstance(ai_amount, (int, float)) else None
    if amount is None:
        _, amount = _parse_amount(price_str)

    # атрибуты по метаданным — без AI
    attrs = suggest_attrs_for_category_fallback(meta, ai_title or title or "", ai_desc or desc or "")
    cat_name = f"Категория {category_id}"
    try:
        status, cats = await svc_list_categories()
        def _find_name(nodes):
            for n in nodes or []:
                if str(n.get("id")) == str(category_id):
                    return n.get("name")
                res = _find_name(n.get("children"))
                if res:
                    return res
            return None
        nice = _find_name(cats if isinstance(cats, list) else [])
        if isinstance(nice, str) and nice.strip():
            cat_name = nice.strip()
    except Exception:
        pass

    attrs = ensure_required_attrs_fallback(meta, attrs, ai_title or title or "", ai_desc or desc or "", category_name=cat_name)
    attrs, report = validate_attribute_map_fallback(meta, attrs, category_name=cat_name)

    # картинки из архива
    files: List[tuple[str, bytes]] = []
    try:
        with ZipFile(path, "r") as z:
            for zi in z.infolist():
                if zi.is_dir():
                    continue
                low = zi.filename.lower()
                if low.endswith("info.txt"):
                    continue
                if not any(low.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
                    continue
                if zi.file_size < 5 * 1024:
                    continue
                if len(files) >= 10:
                    break
                content = z.read(zi)
                files.append((os.path.basename(zi.filename), content))
    except Exception:
        pass

    # загрузка фото
    image_urls: List[str] = []
    if files:
        try:
            st, data_img = await upload_images(username=username, files=files)
            if st == 200 and isinstance(data_img, list):
                image_urls = [u for u in data_img if isinstance(u, str)]
        except Exception:
            pass

    payload = {
        "account": username,
        "title": ai_title or title or "Anzeige",
        "description": ai_desc or desc or "Privatverkauf.",
        "categoryId": str(category_id),
        "priceType": price_type,
        "amount": int(amount or 0),
        "postcode": postcode or "",
        "attributes": attrs or {},
        "images": image_urls,
        "shippingOptions": ["DHL_001", "HERMES_001"],
        "threatmetrix": True,
        "adAddress": "",
        "contact": "",
        "imprint": "",
        "adType": "",
        "id": "",
    }

    try:
        st, resp = await add_ad(payload)
        if st == 200 and (isinstance(resp, dict) and (resp.get("added") or resp.get("ok"))):
            await message.answer(
                "✅ Объявление опубликовано:\n"
                f"• Аккаунт: {username}\n"
                f"• Категория: {cat_name}\n"
                f"• Заголовок: {payload['title']}\n"
                f"• Цена: {payload['amount']} €\n"
            )
        else:
            await message.answer(f"❌ Не удалось опубликовать (статус {st}). Ответ: {str(resp)[:500]}")
    except Exception as e:
        log.exception("add_ad failed")
        await message.answer(f"❌ Ошибка публикации: {e}")
