from aiogram import Router, types
from aiogram.filters import CommandStart
from app.keyboards import main_keyboard

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Это klazfiler.\nВыбери нужный раздел:",
        reply_markup=main_keyboard()
    )
