# app/config.py
from functools import lru_cache
from typing import Optional
from pydantic import HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram
    BOT_TOKEN: str

    # SuitePro API
    SUITEPRO_API_URL: HttpUrl = "https://api.suitepro.to"
    SUITEPRO_API_KEY: str
    SUITEPRO_CATEGORIES_URL: Optional[HttpUrl] = None
    SUITEPRO_METADATA_URL: Optional[HttpUrl] = None
    SUITEPRO_CLASSIFIEDS_URL: Optional[HttpUrl] = None

    # SMS-Activate
    SMS_ACTIVATE_URL: HttpUrl = "https://api.sms-activate.ae/stubs/handler_api.php"
    SMS_ACTIVATE_KEY: str

    # OpenAI (если не используешь — можно оставить пустым)
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_API_BASE: Optional[str] = None
    OPENAI_MODEL: Optional[str] = None

    # Paths
    ARCHIVE_DIR: str = "/opt/klazfiler/archive"

    # HTTP settings
    API_TIMEOUT: int = 120
    API_RETRIES: int = 3

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ВАЖНО: экспортируем готовый синглтон, чтобы `from app.config import settings` работало
settings: Settings = get_settings()
