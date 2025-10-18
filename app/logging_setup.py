import logging
import sys
from pathlib import Path

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: int = logging.INFO):
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(level)

    # очищаем старые хэндлеры
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # глушим лишний шум от внешних библиотек
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.ERROR)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger(name)


def get_registration_logger() -> logging.Logger:
    """Отдельный логгер для регистрации (в консоль + файл)"""
    setup_logging()

    logger = logging.getLogger("klazfiler.registration")
    logger.setLevel(logging.INFO)

    log_path = Path("registration.log")
    if not any(isinstance(h, logging.FileHandler) and h.baseFilename == str(log_path) for h in logger.handlers):
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
        logger.addHandler(fh)

    return logger
