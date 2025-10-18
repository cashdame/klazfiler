import re
import asyncio
from typing import List, Callable, Awaitable, Optional

from aiogram import Router, types, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

from app.keyboards import main_keyboard
from app.services.core import save_ad

router = Router()

class GrabberSG(StatesGroup):
    waiting_url = State()

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

async def _switch_to(target: str, message: types.Message, state: FSMContext):
    # убираем текущее состояние и вызываем нужный раздел через ленивый импорт
    await state.clear()
    await message.answer("Переключаюсь…")
    if target == "list_accounts":
        from app.handlers.list_accounts import list_accounts_open
        await list_accounts_open(message)
    elif target == "registration":
        from app.handlers.registration import registration_entry
        await registration_entry(message, state)
    elif target == "add_account":
        from app.handlers.add_account import addacc_enter
        await addacc_enter(message, state)
    elif target == "archive":
        from app.handlers.archive import open_archive
        await open_archive(message)
    elif target == "quick":
        from app.handlers.menu import quick_publish
        await quick_publish(message)
    elif target == "filters":
        from app.handlers.menu import filters
        await filters(message)
    elif target == "test":
        from app.handlers.menu import test_section
        await test_section(message)
    elif target == "back":
        from app.handlers.menu import back_to_menu
        await back_to_menu(message)

_SWITCH_MAP = {
    "👥 Список аккаунтов": "list_accounts",
    "Список аккаунтов": "list_accounts",
    "📝 Регистрация": "registration",
    "Регистрация": "registration",
    "➕ Добавить аккаунт": "add_account",
    "Добавить аккаунт": "add_account",
    "🗂 Архив товаров": "archive",
    "Архив товаров": "archive",
    "⚡ Быстрая публикация": "quick",
    "Быстрая публикация": "quick",
    "⚙️ Фильтры": "filters",
    "Фильтры": "filters",
    "🔢 123": "test",
    "123": "test",
    "⬅️ Назад": "back",
    "Назад": "back",
}

@router.message(F.text.in_({"📥 Grabber", "Grabber"}))
async def grabber_enter(message: types.Message, state: FSMContext):
    await state.set_state(GrabberSG.waiting_url)
    await message.answer(
        "Режим граббера активирован.\n"
        "Кидай ссылки на объявления — можно несколько сразу.\n"
        "Чтобы выйти, нажми «⬅️ Назад» или отправь /cancel."
    )

@router.message(GrabberSG.waiting_url, F.text.cast(str).as_("text"))
async def grabber_switch_or_process(message: types.Message, state: FSMContext, text: str):
    target = _SWITCH_MAP.get(text)
    if target:
        await _switch_to(target, message, state)
        return

    urls: List[str] = URL_RE.findall(text or "")
    if not urls:
        await message.answer("Жду ссылку. Можно несколько в одном сообщении.\nДля выхода — «⬅️ Назад» или /cancel.")
        return

    for url in urls:
        progress = await message.answer(f"Обрабатываю:\n{url}")
        try:
            path = await asyncio.to_thread(save_ad, url)
            if not path:
                await progress.edit_text(f"Не удалось сохранить объявление:\n{url}")
                continue
            await progress.edit_text("Готово. Шлю архив…")
            await message.answer_document(
                FSInputFile(path),
                caption="Архив готов. Можешь прислать ещё ссылку.\nВыход — «⬅️ Назад» или /cancel."
            )
        except Exception as e:
            await progress.edit_text(f"Ошибка при обработке:\n{url}\n{e}")

@router.message(GrabberSG.waiting_url, F.text.in_({"/cancel", "/stop"}))
async def grabber_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Вышел из режима граббера. Главное меню:", reply_markup=main_keyboard())
