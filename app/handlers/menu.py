#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
from aiogram import types, Dispatcher
from aiogram.dispatcher import FSMContext

from app.keyboards import main_keyboard
from app.handlers.quick_post import quickpost_entry

logger = logging.getLogger("klazfiler.menu")

# Тексты кнопок из твоего keyboards.py
BTN_QUICKPOST = "⚡ Быстрая публикация"
BTN_GRABBER = "📥 Grabber"
BTN_ARCHIVE = "🗂 Архив товаров"
BTN_ADDACC = "➕ Добавить аккаунт"
BTN_LISTACC = "👥 Список аккаунтов"
BTN_REG = "📝 Регистрация"
BTN_BACK = "⬅️ Назад"
BTN_FILTERS = "⚙️ Фильтры"
BTN_123 = "🔢 123"

async def cmd_start(message: types.Message, state: FSMContext):
    if state:
        await state.finish()
    await message.answer("Меню:", reply_markup=main_keyboard())

async def on_menu_message(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()

    if txt == BTN_QUICKPOST:
        await quickpost_entry(message, state)
        return

    if txt == BTN_GRABBER:
        await message.answer("Граббер откроем позже (пока заглушка).")
        return

    if txt == BTN_ARCHIVE:
        await message.answer("Архив откроем позже (пока заглушка).")
        return

    if txt == BTN_ADDACC:
        await message.answer("Добавление аккаунта (пока заглушка).")
        return

    if txt == BTN_LISTACC:
        await message.answer("Список аккаунтов (пока заглушка).")
        return

    if txt == BTN_REG:
        await message.answer("Регистрация (пока заглушка, у тебя уже есть отдельный модуль).")
        return

    if txt in (BTN_BACK, BTN_FILTERS, BTN_123):
        await message.answer("Ок", reply_markup=main_keyboard())
        return

    # если что-то непонятное — просто вернём меню
    await message.answer("Не понял. Давай так:", reply_markup=main_keyboard())

def setup_menu(dp: Dispatcher):
    dp.register_message_handler(cmd_start, commands=["start", "menu"])
    dp.register_message_handler(on_menu_message, content_types=types.ContentTypes.TEXT)
