from aiogram import Router, types
from app.keyboards import main_keyboard

router = Router()

@router.message(lambda m: m.text in {"Меню", "Назад"})
async def menu_handler(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_keyboard())


@router.message(lambda m: m.text == "Быстрая публикация")
async def quick_publish_handler(message: types.Message):
    await message.answer("Раздел Быстрая публикация. Тут будет быстрая отправка объявлений.")

# ВАЖНО: "Добавить аккаунт" ловится в add_account.py

@router.message(lambda m: m.text == "123")
async def test_handler(message: types.Message):
    await message.answer("Раздел 123. Тестовая функция.")

@router.message(lambda m: m.text == "Фильтры")
async def filters_handler(message: types.Message):
    await message.answer("Раздел Фильтры. Здесь будут настройки фильтров.")
