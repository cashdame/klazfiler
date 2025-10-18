# /opt/klazfiler/main.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.logging_setup import setup_logging

# подключаем конкретные роутеры
from app.handlers import (
    list_accounts,
    add_account,
    registration,
    archive,
    grabber,
    menu,        # меню после спец-хендлеров
    fallback,    # fallback в самом конце
)

setup_logging()
log = logging.getLogger("klazfiler")

async def main():
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # порядок важен: спец-хендлеры -> меню -> fallback
    dp.include_router(list_accounts.router)
    dp.include_router(add_account.router)
    dp.include_router(registration.router)
    dp.include_router(archive.router)
    dp.include_router(grabber.router)
    dp.include_router(menu.router)
    dp.include_router(fallback.router)

    log.info("klazfiler запущен и слушает Telegram API...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.warning("klazfiler остановлен вручную.")
