import os
import tempfile
from io import BytesIO

from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from app.keyboards import main_keyboard
from app.logging_setup import get_logger
from app.tools.registration_bot import run_registration_batch

router = Router()
log = get_logger("klazfiler.registration")

class RegStates(StatesGroup):
    waiting_file = State()

def reg_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Назад")]],
        resize_keyboard=True,
    )

async def _switch_to(target: str, message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Переключаюсь…")
    if target == "list_accounts":
        from app.handlers.list_accounts import list_accounts_open
        await list_accounts_open(message)
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

@router.message(F.text.in_({"📝 Регистрация", "Регистрация"}))
async def registration_entry(message: Message, state: FSMContext):
    await state.set_state(RegStates.waiting_file)
    log.info("enter registration: waiting_file")
    await message.answer(
        "Пришли .txt со списком почт (email:pass, по строкам или через |).\n"
        "Отправь как документ (скрепка).",
        reply_markup=reg_keyboard(),
    )

# 1) СНАЧАЛА хендлер документов (иначе текстовый перехватит событие)
@router.message(RegStates.waiting_file, F.document)
async def registration_file_received(message: Message, state: FSMContext):
    log.info("registration: document handler triggered")
    document = message.document
    file_name = (document.file_name or "mails.txt")
    lower_name = file_name.lower()
    mime = (document.mime_type or "").lower()

    if not (lower_name.endswith(".txt") or mime.startswith("text/")):
        await message.answer("Нужен текстовый файл .txt. Пришли ещё раз как документ.")
        return

    try:
        await message.answer("⬇️ Скачиваю файл…")
        # пробуем нативную загрузку
        try:
            with tempfile.TemporaryDirectory(prefix="reg_") as tmpdir:
                local_path = os.path.join(tmpdir, lower_name if lower_name.endswith(".txt") else "mails.txt")
                await message.bot.download(document, destination=local_path)

                async def notify(text: str) -> None:
                    try:
                        await message.answer(text)
                    except Exception as e:
                        log.warning("notify failed: %s", e)

                await notify("✅ Файл получен. Запускаю регистрацию…")
                await run_registration_batch(file_path=local_path, notify=notify)

        except Exception:
            # fallback для пересланных документов
            tg_file = await message.bot.get_file(document.file_id)
            buf = BytesIO()
            await message.bot.download_file(tg_file.file_path, buf)
            buf.seek(0)
            with tempfile.TemporaryDirectory(prefix="reg_") as tmpdir:
                local_path = os.path.join(tmpdir, lower_name if lower_name.endswith(".txt") else "mails.txt")
                with open(local_path, "wb") as f:
                    f.write(buf.read())

                async def notify(text: str) -> None:
                    try:
                        await message.answer(text)
                    except Exception as e:
                        log.warning("notify failed: %s", e)

                await message.answer("✅ Файл получен. Запускаю регистрацию…")
                await run_registration_batch(file_path=local_path, notify=notify)

    except Exception as e:
        log.exception("Ошибка при скачивании/обработке файла: %s", e)
        await message.answer(f"❌ Не удалось обработать файл: {e}")

    await state.set_state(RegStates.waiting_file)
    await message.answer("Если нужно — пришли ещё один .txt.\nИли «⬅️ Назад» для выхода.", reply_markup=reg_keyboard())

# 2) ПОТОМ текст — и только если это НЕ документ
@router.message(RegStates.waiting_file, ~F.document, F.text.cast(str).as_("text"))
async def registration_switch_or_prompt(message: Message, state: FSMContext, text: str):
    log.info("registration: text handler triggered")
    target = _SWITCH_MAP.get(text)
    if target:
        await _switch_to(target, message, state)
    else:
        await message.answer("Жду .txt-файл как документ. Или «⬅️ Назад».")
