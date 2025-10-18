from aiogram import Router, types
from app.keyboards import main_keyboard

router = Router()

@router.message()
async def fallback(message: types.Message):
    await message.answer("Не понял. Выбери кнопку ниже.", reply_markup=main_keyboard())
