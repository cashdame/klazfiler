import asyncio
from aiogram import Router, F
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from app.logging_setup import get_logger
from app.registration_bot import run_registration_batch

router = Router()
log = get_logger("klazfiler")

class RegStates(StatesGroup):
    waiting_file = State()
    waiting_limit = State()

def reg_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Стоп")],
            [KeyboardButton(text="Назад в меню")],
        ],
        resize_keyboard=True
    )

@router.message(F.text.casefold() == "регистрация")
async def registration_entry(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(RegStates.waiting_file)
    await message.answer(
        "Отправь .txt с почтами в формате mail:pass|mail2:pass2|...",
        reply_markup=reg_keyboard(),
    )

@router.message(RegStates.waiting_file, F.content_type == ContentType.DOCUMENT)
async def got_file(message: Message, state: FSMContext):
    if not message.document or not message.document.file_name.endswith(".txt"):
        await message.answer("Нужен .txt файл. Пришли заново.")
        return

    file = await message.bot.get_file(message.document.file_id)
    file_path = file.file_path
    dest_path = f"/tmp/{message.document.file_unique_id}.txt"

    await message.bot.download_file(file_path, destination=dest_path)
    log.info("Получен файл с почтами: %s -> %s", message.document.file_name, dest_path)

    await state.update_data(mails_path=dest_path)
    await state.set_state(RegStates.waiting_limit)
    await message.answer("Сколько регистрируем? Введи число или напиши 'все'.")

@router.message(RegStates.waiting_file)
async def waiting_file_hint(message: Message, state: FSMContext):
    await message.answer("Пришли .txt файл с почтами.")

@router.message(RegStates.waiting_limit, F.text.casefold().in_({"все", "всё"}))
async def reg_limit_all(message: Message, state: FSMContext):
    data = await state.get_data()
    path = data.get("mails_path")
    if not path:
        await message.answer("Файл не найден. Начни заново: Регистрация.")
        await state.clear()
        return

    await message.answer("Принято: регистрируем все почты. Запускаю регистрацию...")
    asyncio.create_task(run_registration_batch(message.bot, message.chat.id, path, None))
    await state.clear()

@router.message(RegStates.waiting_limit, F.text.regexp(r"^\d+$"))
async def reg_limit_number(message: Message, state: FSMContext):
    try:
        limit = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно число, например 10, или слово 'все'.")
        return

    data = await state.get_data()
    path = data.get("mails_path")
    if not path:
        await message.answer("Файл не найден. Начни заново: Регистрация.")
        await state.clear()
        return

    await message.answer(f"Принято: лимит {limit}. Запускаю регистрацию...")
    asyncio.create_task(run_registration_batch(message.bot, message.chat.id, path, limit))
    await state.clear()

@router.message(RegStates.waiting_limit)
async def waiting_limit_hint(message: Message, state: FSMContext):
    await message.answer("Введи число или напиши 'все'.")
