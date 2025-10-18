# /opt/klazfiler/app/handlers/menu.py
#!/usr/bin/env python3
from aiogram import Router, F, types
from app.keyboards import main_keyboard

# реальные точки входа из модулей
from app.handlers.list_accounts import list_accounts_open
from app.handlers.add_account import addacc_enter
from app.handlers.registration import registration_entry
from app.handlers.archive import open_archive
from app.handlers.grabber import grabber_enter
from app.tools.quickpost import quickpost_entry  # файл должен лежать в app/tools/quickpost.py

router = Router(name="menu")

BTN_QUICKPOST = "⚡ Быстрая публикация"
BTN_GRABBER   = "📥 Grabber"
BTN_ARCHIVE   = "🗂 Архив товаров"
BTN_ADDACC    = "➕ Добавить аккаунт"
BTN_LISTACC   = "👥 Список аккаунтов"
BTN_REG       = "📝 Регистрация"
BTN_BACK      = "⬅️ Назад"

@router.message(F.text.in_({"/start", "/menu"}))
async def cmd_start(message: types.Message):
    await message.answer("Меню:", reply_markup=main_keyboard())

# точечные хендлеры по кнопкам (без общего ловца F.text)
@router.message(F.text.in_({BTN_GRABBER, "Grabber"}))
async def _grabber(message: types.Message):
    # grabber сам переведёт в своё состояние
    await grabber_enter(message, state=None)

@router.message(F.text == BTN_QUICKPOST)
async def _quickpost(message: types.Message):
    await quickpost_entry(message)

@router.message(F.text.in_({BTN_LISTACC, "Список аккаунтов"}))
async def _listacc(message: types.Message):
    await list_accounts_open(message)

@router.message(F.text.in_({BTN_ADDACC, "Добавить аккаунт"}))
async def _addacc(message: types.Message):
    await addacc_enter(message, state=None)

@router.message(F.text.in_({BTN_REG, "Регистрация"}))
async def _reg(message: types.Message):
    await registration_entry(message, state=None)

@router.message(F.text == BTN_ARCHIVE)
async def _archive(message: types.Message):
    await open_archive(message)

@router.message(F.text == BTN_BACK)
async def _back(message: types.Message):
    await message.answer("Ок", reply_markup=main_keyboard())
