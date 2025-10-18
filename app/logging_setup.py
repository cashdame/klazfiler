import logging
import sys

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False

def setup_logging(level: int = logging.INFO):
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.setLevel(level)

    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.ERROR)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger(name)

def get_registration_logger():
    """Логгер для регистратора с выводом в консоль и файл"""
    setup_logging()  # гарантируем, что общая конфигурация есть
    logger = logging.getLogger("registration")
    logger.setLevel(logging.INFO)

    # если уже есть file handler — не дублируем
    if not any(isinstance(h, logging.FileHandler) and h.baseFilename.endswith("registration.log") for h in logger.handlers):
        fh = logging.FileHandler("registration.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(fh)

    return logger

