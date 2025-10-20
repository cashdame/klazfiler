# app/handlers/archive.py
# -*- coding: utf-8 -*-
"""
Обработчик «Архив».
Функции: список, просмотр, скачивание, удаление, закрытие.
Кнопка «Опубликовать» просто передаёт управление во внешний мастер.
"""

import os
import re
import logging
import hashlib
from contextlib import suppress
from zipfile import ZipFile
from typing import List, Tuple, Optional

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)

from app.config import settings
from app.tools.archive_publish import (
    start_publish_from_archive,
    handle_account_pick_cb,
    handle_account_page_cb,
)

router = Router()
log = logging.getLogger("klazfiler.archive")

PAGE_SIZE = 10

# -------------------- утилиты --------------------

def _archive_dir() -> str:
    d = (getattr(settings, "ARCHIVE_DIR", "") or "").strip()
    return d or os.getcwd()

def _list_archives() -> List[str]:
    d = _archive_dir()
    files: List[str] = []
    with suppress(Exception):
        for name in os.listdir(d):
            if name.lower().endswith(".zip"):
                files.append(name)
    files.sort(key=lambda n: os.path.getmtime(os.path.join(d, n)), reverse=True)
    return files

def _id_for(name: str) -> str:
    # короткий id для callback_data (<= 64 байт)
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]

def _find_name_by_id(file_id: str) -> Optional[str]:
    for name in _list_archives():
        if _id_for(name) == file_id:
            return name
    return None

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
            import re as _re
            m_title = _re.search(r"^Title:\s*(.+)$", text, _re.M)
            m_price = _re.search(r"^Price:\s*(.+)$", text, _re.M)
            m_url = _re.search(r"^URL:\s*(.+)$", text, _re.M)
            m_saved = _re.search(r"^Saved at:\s*(.+)$", text, _re.M)
            m_desc = _re.search(r"^Description:\s*\n([\s\S]+)$", text, _re.M)
            title = (m_title.group(1).strip() if m_title else "")
            price = (m_price.group(1).strip() if m_price else "")
            url = (m_url.group(1).strip() if m_url else "")
            saved = (m_saved.group(1).strip() if m_saved else "")
            desc = (m_desc.group(1).strip() if m_desc else "")
    except Exception:
        pass
    return title, price, url, saved, desc

# -------------------- клавиатуры --------------------

def _kb_archive_page(page: int) -> InlineKeyboardMarkup:
    items = _list_archives()
    total = len(items)
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    slice_items = items[start:end]
    rows = []
    for name in slice_items:
        fid = _id_for(name)
        rows.append([InlineKeyboardButton(text=name[:60], callback_data=f"arch:open:{fid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⟨ Назад", callback_data=f"arch:page:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton(text="Вперёд ⟩", callback_data=f"arch:page:{page+1}"))
    rows.append(nav or [InlineKeyboardButton(text="Закрыть", callback_data="arch:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_archive_item(name: str) -> InlineKeyboardMarkup:
    fid = _id_for(name)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Опубликовать", callback_data=f"arch:publish:{fid}")],
        [InlineKeyboardButton(text="⬇️ Скачать", callback_data=f"arch:send:{fid}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"arch:delete:{fid}")],
        [InlineKeyboardButton(text="← Назад к списку", callback_data="arch:page:0")],
    ])

# -------------------- обработчики --------------------

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
    file_id = call.data.split(":", 2)[-1]
    name = _find_name_by_id(file_id)
    if not name:
        await call.answer("Файл не найден", show_alert=True)
        return
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
    file_id = call.data.split(":", 2)[-1]
    name = _find_name_by_id(file_id)
    if not name:
        await call.answer("Файл не найден", show_alert=True)
        return
    path = os.path.join(_archive_dir(), name)
    with suppress(Exception):
        await call.message.answer_document(FSInputFile(path))
    await call.answer()

@router.callback_query(F.data.startswith("arch:delete:"))
async def archive_delete(call: CallbackQuery) -> None:
    file_id = call.data.split(":", 2)[-1]
    name = _find_name_by_id(file_id)
    if not name:
        await call.answer("Файл не найден", show_alert=True)
        return
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

# --- публикация: проксирование во внешний модуль ---

@router.callback_query(F.data.startswith("arch:publish:"))
async def archive_publish_forward(call: CallbackQuery) -> None:
    file_id = call.data.split(":", 2)[-1]
    name = _find_name_by_id(file_id)
    if not name:
        await call.answer("Файл не найден", show_alert=True)
        return
    path = os.path.join(_archive_dir(), name)
    await start_publish_from_archive(call, archive_path=path, archive_name=name)

@router.callback_query(F.data.startswith("apub:pick:"))
async def apub_pick(call: CallbackQuery) -> None:
    await handle_account_pick_cb(call)

@router.callback_query(F.data.startswith("apub:page:"))
async def apub_page(call: CallbackQuery) -> None:
    await handle_account_page_cb(call)

# --- совместимость для menu.py ---
async def open_archive(message: Message):
    await archive_open(message)
