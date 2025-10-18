from __future__ import annotations

from math import ceil
from typing import Dict, List, Optional

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from app.keyboards import main_keyboard
from app.services.accounts import list_accounts as svc_list_accounts

router = Router()

PAGE_SIZE = 12
BTN_COLS = 2

def _check(v: bool) -> str:
    return "✅" if v else "❌"

def _format_account(idx: int, acc: Dict) -> str:
    username = acc.get("username", "-")
    type_ = acc.get("accountType", "-")
    logged = _check(bool(acc.get("loggedIn")))
    verified = _check(bool(acc.get("verified")))
    ads = acc.get("adsCount", 0)
    conv = acc.get("conversationsCount", 0)
    created = acc.get("creationDate", "-")
    return (
        f"{idx}. {username} • {type_} • in {logged} • Ads {ads} • Conv {conv} • verified {verified}\n"
        f"• {created}"
    )

def _kb(accounts: List[Dict], page: int, total: Optional[int], has_more: bool) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []

    row: List[InlineKeyboardButton] = []
    for acc in accounts:
        text = str(acc.get("username", "-"))[:32] or "-"
        row.append(InlineKeyboardButton(text=text, callback_data="accs:none"))
        if len(row) >= BTN_COLS:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav: List[InlineKeyboardButton] = []
    has_prev = page > 0
    if total is not None and isinstance(total, int):
        total_pages = max(1, ceil(total / PAGE_SIZE))
        has_next = page + 1 < total_pages
    else:
        has_next = has_more

    if has_prev:
        nav.append(InlineKeyboardButton(text="⟨ Назад", callback_data=f"accs:page:{page-1}"))
    nav.append(InlineKeyboardButton(text="🔁 Обновить", callback_data=f"accs:refresh:{page}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="Вперёд ⟩", callback_data=f"accs:page:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="Назад в меню", callback_data="accs:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def _render_page_msg(message: Message, page: int, *, edit_message: Message | None = None) -> None:
    limit = PAGE_SIZE
    cursor = page * limit
    status, resp = await svc_list_accounts(cursor=cursor, limit=limit)

    if status == 200 and isinstance(resp, dict):
        accounts = resp.get("accounts") or []
        total = resp.get("totalCount")
        logged_in = resp.get("loggedInCount", "-")
        has_more = bool(resp.get("hasMore"))

        header = f"Список аккаунтов (всего: {total if total is not None else '-'}, залогинены: {logged_in}):"
        lines = [header] + [_format_account(i, acc) for i, acc in enumerate(accounts, start=1 + cursor)]
        text = "\n".join(lines)

        kb = _kb(accounts, page, total if isinstance(total, int) else None, has_more)

        if edit_message:
            await edit_message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        else:
            await message.answer(text, reply_markup=kb, disable_web_page_preview=True)
        return

    err = resp.get("message") if isinstance(resp, dict) else str(resp)
    if edit_message:
        await edit_message.edit_text(f"❌ Не удалось получить список аккаунтов.\n{err}")
    else:
        await message.answer(f"❌ Не удалось получить список аккаунтов.\n{err}")

@router.message(F.text.in_({"👥 Список аккаунтов", "Список аккаунтов"}))
async def list_accounts_open(message: Message) -> None:
    loading = await message.answer("Загружаю список…")
    await _render_page_msg(message, page=0, edit_message=loading)

@router.callback_query(F.data.startswith("accs:page:"))
async def list_accounts_page(call: CallbackQuery) -> None:
    try:
        page = int(call.data.split(":")[-1])
    except Exception:
        await call.answer()
        return
    await _render_page_msg(call.message, page=page, edit_message=call.message)
    await call.answer()

@router.callback_query(F.data.startswith("accs:refresh:"))
async def list_accounts_refresh(call: CallbackQuery) -> None:
    try:
        page = int(call.data.split(":")[-1])
    except Exception:
        page = 0
    await _render_page_msg(call.message, page=page, edit_message=call.message)
    await call.answer("Обновил")

@router.callback_query(F.data == "accs:none")
async def list_accounts_noop(call: CallbackQuery) -> None:
    await call.answer()

@router.callback_query(F.data == "accs:back")
async def list_accounts_back(call: CallbackQuery) -> None:
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.message.answer("Главное меню.", reply_markup=main_keyboard())
    await call.answer()
