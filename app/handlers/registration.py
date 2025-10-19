# app/handlers/registration.py
# v2: спрашиваем количество, арендуем почты на SMS-Activate и запускаем регистрацию
import re
import asyncio
from typing import Callable, Awaitable

from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from app.keyboards import main_keyboard
from app.logging_setup import get_logger
# ВАЖНО: функция уже должна быть в app/tools/registration_bot.py (v2)
from app.tools.registration_bot import run_registration_rent_batch

router = Router()
log = get_logger("klazfiler.registration.v2")


class RegStates(StatesGroup):
    waiting_count = State()


def reg_keyboard() -> ReplyKeyboardMarkup:
    # Минималистично: только «Назад»
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Назад")]],
        resize_keyboard=True,
    )


async def _switch_to(target: str, message: Message, state: FSMContext):
    await state.clear()
    if target == "back":
        await message.answer("Меню:", reply_markup=main_keyboard())
    else:
        await message.answer("Меню:", reply_markup=main_keyboard())


@router.message(F.text.in_({"📝 Регистрация", "Регистрация"}))
async def registration_entry(message: Message, state: FSMContext):
    # Старт v2: спрашиваем, сколько почт арендовать
    await state.set_state(RegStates.waiting_count)
    log.info("enter registration v2: waiting_count")
    await message.answer(
        "Сколько почт арендовать под регистрацию? Введи число, например 4.",
        reply_markup=reg_keyboard(),
    )


@router.message(RegStates.waiting_count, F.text.regexp(r"^\d{1,3}$"))
async def handle_count(message: Message, state: FSMContext):
    await state.clear()
    count = int(message.text)
    if count <= 0:
        await message.answer("Нужно положительное число. Попробуй ещё раз.")
        await state.set_state(RegStates.waiting_count)
        return

    # создаём первое сообщение со статусом
    status_msg = await message.answer(f"⏳ Покупаю {count} почт...")

    start_time = asyncio.get_event_loop().time()
    status = {
        "total": count,
        "done": 0,
        "failed": 0,
        "stage": "инициализация",
    }

    async def notify(text: str) -> None:
        """редактирует одно сообщение по мере прогресса"""
        nonlocal status
        try:
            if "куплено" in text.lower():
                status["stage"] = "куплено"
            elif "init" in text.lower() or "регистрирую" in text.lower():
                status["stage"] = "регистрирую"
            elif "ссылка" in text.lower():
                status["stage"] = "письмо получено"
            elif "sms" in text.lower():
                status["stage"] = "смс код"
            elif text.startswith("✅"):
                status["done"] += 1
            elif "❌" in text:
                status["failed"] += 1

            elapsed = int(asyncio.get_event_loop().time() - start_time)
            mins, secs = divmod(elapsed, 60)
            summary = (
                f"Куплено: {status['total']} почт\n"
                f"Готово: {status['done']} / Ошибок: {status['failed']}\n"
                f"Стадия: {status['stage']}\n"
                f"⏱ Время: {mins}м {secs}с"
            )
            await status_msg.edit_text(summary)
        except Exception as e:
            log.warning("notify edit failed: %s", e)

    try:
        await run_registration_rent_batch(count=count, notify=notify)
    except Exception as e:
        log.exception("run_registration_rent_batch error: %s", e)
        await status_msg.edit_text(f"❌ Ошибка запуска регистрации: {e}")
        return

    elapsed = int(asyncio.get_event_loop().time() - start_time)
    mins, secs = divmod(elapsed, 60)
    await status_msg.edit_text(
        f"✅ Готово!\n"
        f"Куплено: {status['total']} почт\n"
        f"Зарегистрировано: {status['done']}\n"
        f"Не удалось: {status['failed']}\n"
        f"⏱ Всего заняло: {mins}м {secs}с"
    )

    # можно сразу предложить повторить
    await state.set_state(RegStates.waiting_count)
    await message.answer(
        "Хочешь ещё? Введи новое число или «⬅️ Назад».",
        reply_markup=reg_keyboard(),
    )

@router.message(RegStates.waiting_count)
async def registration_switch_or_prompt(message: Message, state: FSMContext):
    # Поддержка «Назад», всё остальное — просьба ввести число
    if message.text in {"⬅️ Назад", "Назад"}:
        await _switch_to("back", message, state)
        return
    if re.fullmatch(r"\d{1,3}", message.text or ""):
        # На всякий случай — если регексп-селектор не сработал
        await handle_count(message, state)
        return
    await message.answer("Введи просто число, например 2. Или «⬅️ Назад».")
