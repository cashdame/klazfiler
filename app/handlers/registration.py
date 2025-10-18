# app/handlers/registration.py
import asyncio
import os
import tempfile
from typing import Callable, Awaitable

from aiogram import Router, F
from aiogram.types import (
    Message,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ContentType,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from app.keyboards import main_keyboard
from app.logging_setup import get_logger
from app.registration_bot import run_registration_batch

router = Router()
log = get_logger("klazfiler.registration")


# --------- FSM ----------
class RegStates(StatesGroup):
    idle = State()
    waiting_file = State()


# --------- Keyboards ----------
def reg_keyboard() -> ReplyKeyboardMarkup:
    """
    Локальное меню регистрации.
    Обязательно передаём поле `keyboard`, иначе pydantic у aiogram 3 валится.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Старт регистрации")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


# --------- Helpers ----------
async def _notify(message: Message, text: str) -> None:
    try:
        await message.answer(text)
    except Exception as e:
        log.warning("Не удалось отправить уведомление в чат: %s", e)


def _build_notifier(message: Message) -> Callable[[str], Awaitable[None]]:
    """Вернёт колбэк, который registration_bot сможет вызывать для промежуточных сообщений."""
    async def _inner(text: str) -> None:
        await _notify(message, text)
    return _inner


async def _run_batch_with_file(message: Message, file_path: str) -> None:
    """
    Запускаем партию регистраций.
    Предполагаем, что внутри run_registration_batch реализованы:
      - лимит 2 регистрации за запуск,
      - пауза 11 минут между батчами (или она не нужна, если батч один),
      - пошаговые уведомления через переданный notify().
    """
    notify = _build_notifier(message)

    await notify("✅ Файл получен. Начинаю обработку…")
    try:
        # Если run_registration_batch синхронная, можно обернуть:
        # await asyncio.to_thread(run_registration_batch, file_path=file_path, notify=notify)
        await run_registration_batch(file_path=file_path, notify=notify)
    except Exception as e:
        log.exception("Ошибка при запуске регистрации: %s", e)
        await notify(f"❌ Ошибка при регистрации: {e}")
        return

    await notify("🏁 Готово. Партия регистраций завершена.")


# --------- Entry points ----------
@router.message(F.text == "📝 Регистрация")
async def registration_entry(message: Message, state: FSMContext):
    """
    Точка входа из главного меню.
    Показываем подменю регистрации.
    """
    await state.set_state(RegStates.idle)
    await message.answer(
        "Раздел «Регистрация». Выберите действие:",
        reply_markup=reg_keyboard(),
    )


@router.message(F.text == "🚀 Старт регистрации")
async def registration_start(message: Message, state: FSMContext):
    """
    Просим прислать .txt со списком почт в формате:
      mail:pass|
      mail2:pass2|
    """
    await state.set_state(RegStates.waiting_file)
    await message.answer(
        "Пришлите .txt-файл со списком почт в формате:\n"
        "`mail:pass|` (каждая пара на одной строке или через `|`).\n\n"
        "Файл — как *документ* (скрепка).",
        reply_markup=reg_keyboard(),
        parse_mode="Markdown",
    )


@router.message(F.text == "⬅️ Назад")
async def registration_back_to_menu(message: Message, state: FSMContext):
    """
    Возврат в главное меню бота.
    """
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_keyboard())


# --------- File handler ----------
@router.message(RegStates.waiting_file, F.content_type == ContentType.DOCUMENT)
async def registration_file_received(message: Message, state: FSMContext):
    """
    Обрабатываем присланный .txt как документ.
    Сохраняем во временный файл и передаём в движок регистрации.
    """
    document = message.document

    # Простая валидация типа/расширения
    filename = (document.file_name or "").lower()
    if not filename.endswith(".txt"):
        await message.answer("Нужен текстовый файл с расширением .txt. Пришлите ещё раз.")
        return

    # Скачиваем во временный файл
    try:
        with tempfile.TemporaryDirectory(prefix="reg_") as tmpdir:
            local_path = os.path.join(tmpdir, filename or "emails.txt")
            await message.answer("⬇️ Скачиваю файл…")
            await message.bot.download(document, destination=local_path)

            await _run_batch_with_file(message, local_path)

    except Exception as e:
        log.exception("Ошибка при скачивании/обработке файла: %s", e)
        await message.answer(f"❌ Не удалось обработать файл: {e}")

    finally:
        # Возвращаемся в режим ожидания нового файла, чтобы можно было слать следующий
        await state.set_state(RegStates.waiting_file)
        await message.answer(
            "Если хотите обработать ещё один файл — пришлите его.\n"
            "Или нажмите «⬅️ Назад» для выхода.",
            reply_markup=reg_keyboard(),
        )


# Страховка: если пользователь шлёт не документ в состоянии ожидания файла
@router.message(RegStates.waiting_file)
async def registration_waiting_wrong_content(message: Message):
    await message.answer("Мне нужен .txt-файл со списком почт. Пришлите его как документ (скрепка).")
