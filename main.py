import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.logging_setup import setup_logging, get_logger
from app.config import settings

from app.handlers import (
    start,
    menu,
    grabber,
    archive,
    list_accounts,
    add_account,
    registration,
    fallback,
)

async def main():
    setup_logging()
    log = get_logger("klazfiler")

    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан в .env")

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.include_router(start.router)
    dp.include_router(menu.router)
    dp.include_router(grabber.router)
    dp.include_router(archive.router)
    dp.include_router(list_accounts.router)
    dp.include_router(add_account.router)
    dp.include_router(registration.router)
    dp.include_router(fallback.router)

    log.info("klazfiler запущен и слушает Telegram API...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
