import asyncio
import random
from typing import Optional, List, Tuple
from aiogram import Bot
from app.logging_setup import get_logger

log = get_logger("klazfiler.registration")

COUNTRY_CODES = [34, 117, 49, 56]
BATCH_SIZE = 2
BATCH_PAUSE_SEC = 11 * 60

def _parse_mails(content: str) -> List[Tuple[str, str]]:
    raw = content.replace("\n", "|").replace("\r", "|")
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    pairs: List[Tuple[str, str]] = []
    for p in parts:
        if ":" in p:
            mail, pwd = p.split(":", 1)
            mail, pwd = mail.strip(), pwd.strip()
            if mail and pwd:
                pairs.append((mail, pwd))
    return pairs

async def dummy_register(mail: str, pwd: str, country: int) -> bool:
    await asyncio.sleep(0.5)
    await asyncio.sleep(0.5)
    await asyncio.sleep(0.5)
    await asyncio.sleep(0.5)
    return True

async def run_registration_batch(bot: Bot, chat_id: int, mails_path: str, limit: Optional[int] = None):
    log.info("Старт пачки регистрации: file=%s, limit=%s", mails_path, limit)
    try:
        with open(mails_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        log.error("Не удалось прочитать файл почт: %s", e)
        await bot.send_message(chat_id, f"Ошибка чтения файла: {e}")
        return

    pairs = _parse_mails(content)
    if not pairs:
        await bot.send_message(chat_id, "Файл пустой или формат не распознан.")
        return

    if isinstance(limit, int) and limit > 0:
        pairs = pairs[:limit]

    total = len(pairs)
    success = 0
    failed = 0

    await bot.send_message(chat_id, f"Начинаю регистрацию. Почт: {total}. Пакетами по {BATCH_SIZE} каждые 11 минут.")

    for i in range(0, total, BATCH_SIZE):
        batch = pairs[i:i+BATCH_SIZE]
        await bot.send_message(chat_id, f"Пакет {i//BATCH_SIZE + 1}: {len(batch)} аккаунта(ов).")
        for mail, pwd in batch:
            cc = random.choice(COUNTRY_CODES)
            try:
                await bot.send_message(chat_id, f"Выбрал почту: {mail} (код страны {cc})")
                ok = await dummy_register(mail, pwd, cc)
                if ok:
                    success += 1
                    await bot.send_message(chat_id, f"Готово: {mail} зарегистрирован ✅")
                else:
                    failed += 1
                    await bot.send_message(chat_id, f"Не удалось: {mail} ❌")
            except Exception as e:
                log.exception("Ошибка при регистрации %s: %s", mail, e)
                failed += 1
                await bot.send_message(chat_id, f"Ошибка при регистрации {mail}: {e}")

        if i + BATCH_SIZE < total:
            await bot.send_message(chat_id, "Пауза 11 минут перед следующими двумя...")
            await asyncio.sleep(BATCH_PAUSE_SEC)

    await bot.send_message(chat_id, f"Готово. Успешно: {success}, неуспешно: {failed}. Всего: {total}.")
    log.info("Регистрация окончена. Успех=%s, Провал=%s, Всего=%s", success, failed, total)
