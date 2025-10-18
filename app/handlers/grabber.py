# /opt/klazfiler/app/handlers/grabber.py
import re
import asyncio
from typing import List

from aiogram import Router, types
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

from app.keyboards import main_keyboard
from app.services.core import save_ad

router = Router()

# Состояние «жду ссылки, работаю в режиме граббера»
class GrabberSG(StatesGroup):
    waiting_url = State()

# Находим все URL в сообщении
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

@router.message(lambda m: m.text == "Grabber")
async def grabber_enter(message: types.Message, state: FSMContext):
    # Входим в режим и остаёмся в нём, пока пользователь не выйдет
    await state.set_state(GrabberSG.waiting_url)
    await message.answer(
        "Режим граббера активирован.\n"
        "Кидай ссылки на объявления (Kleinanzeigen / eBay / Willhaben) — можно сразу несколько, хоть по одной строке.\n"
        "Чтобы выйти, нажми «Назад» или отправь /cancel."
    )

@router.message(GrabberSG.waiting_url, lambda m: m.text in {"Назад", "Меню", "/cancel", "/stop"})
async def grabber_exit(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Вышел из режима граббера. Главное меню:", reply_markup=main_keyboard())

@router.message(GrabberSG.waiting_url)
async def grabber_process(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    urls: List[str] = URL_RE.findall(text)

    if not urls:
        await message.answer("Жду ссылку. Можно отправлять по одной или несколько в сообщении.\nДля выхода — «Назад» или /cancel.")
        return

    # Обрабатываем по очереди, чтобы не забивать сеть
    for url in urls:
        progress = await message.answer(f"Обрабатываю:\n{url}")
        try:
            path = await asyncio.to_thread(save_ad, url)
            if not path:
                await progress.edit_text(f"Не удалось сохранить объявление:\n{url}")
                continue

            await progress.edit_text("Готово. Шлю архив…")
            await message.answer_document(
                FSInputFile(path),
                caption="Архив объявления сохранён. Можно прислать ещё ссылку.\nДля выхода — «Назад» или /cancel."
            )
        except Exception as e:
            await progress.edit_text(f"Ошибка при обработке ссылки:\n{url}\n{e}")

    # ВАЖНО: состояние НЕ очищаем — остаёмся в режиме до явного выхода
