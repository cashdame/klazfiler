from aiogram import Router, types, F
from app.keyboards import main_keyboard

router = Router()

# --- только общий "Назад" и разделы без собственных модулей ---

@router.message(F.text.in_({"⬅️ Назад", "Назад", "Меню", "⬅️ Назад в меню"}))
async def back_to_menu(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_keyboard())

# Эти разделы пока как заглушки — у них нет своих отдельных модулей:
@router.message(F.text.in_({"⚡ Быстрая публикация", "Быстрая публикация"}))
async def quick_publish(message: types.Message):
    await message.answer("Быстрая публикация — заглушка. Позже добавим мастер публикации.")

@router.message(F.text.in_({"🧮 Профит", "Профит"}))
async def profit(message: types.Message):
    await message.answer("Профит — заглушка. Введём цену и процент, посчитаем наценку.")

@router.message(F.text.in_({"⚙️ Фильтры", "Фильтры"}))
async def filters(message: types.Message):
    await message.answer("Фильтры — заглушка. Здесь будут настройки отбора.")

@router.message(F.text.in_({"🔢 123", "123"}))
async def test_section(message: types.Message):
    await message.answer("Раздел 123. Тест.")
