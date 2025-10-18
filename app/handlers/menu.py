#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
from aiogram import Router, F, types

from app.keyboards import main_keyboard
# если у тебя есть модуль быстропоста — импортируем точку входа
try:
    from app.handlers.quick_post import quickpost_entry
except Exception:
    quickpost_entry = None  # переживём временно

logger = logging.getLogger("klazfiler.menu")

# aiogram v3: нужен Router
router = Router(name="menu")

# Тексты кнопок (держим здесь, чтобы не трогать keyboards.py)
BTN_QUICKPOST = "⚡ Быстрая публикация"
BTN_GRABBER   = "📥 Grabber"
BTN_ARCHIVE   = "🗂 Архив товаров"
BTN_ADDACC    = "➕ Добавить аккаунт"
BTN_LISTACC   = "👥 Список аккаунтов"
BTN_REG       = "📝 Регистрация"
BTN_BACK      = "⬅️ Назад"
BTN_FILTERS   = "⚙️ Фильтры"
BTN_123       = "🔢 123"

# /start и /menu
@router.message(F.text.as_("text") & (F.text == "/start") | (F.text == "/menu"))
async def cmd_start(message: types.Message, text: str):
    await message.answer("Меню:", reply_markup=main_keyboard())

# Обработка нажатий по тексту кнопок
@router.message(F.text)
async def on_menu_message(message: types.Message):
    txt = (message.text or "").strip()

    if txt == BTN_QUICKPOST:
        if quickpost_entry is None:
            await message.answer("Быстрая публикация пока недоступна (модуль не найден).")
            return
        await quickpost_entry(message)
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
        await message.answer("Регистрация — у тебя есть отдельный модуль. Откроем оттуда.")
        return

    if txt in (BTN_BACK, BTN_FILTERS, BTN_123):
        await message.answer("Ок", reply_markup=main_keyboard())
        return

    # если пришло что-то постороннее — просто покажем меню
    await message.answer("Не понял. Давай так:", reply_markup=main_keyboard())
