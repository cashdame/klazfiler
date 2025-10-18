from aiogram import Router, F
from aiogram.types import Message
from app.keyboards import main_keyboard

router = Router()

@router.message(F.text == "Архив товаров")
async def open_archive(message: Message):
    await message.answer(
        "Раздел Архив товаров. Тут будут сохранённые объявления.",
        reply_markup=main_keyboard()
    )
