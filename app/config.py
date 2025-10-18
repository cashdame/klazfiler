# app/config.py
from __future__ import annotations
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # OpenAI
    OPENAI_API_KEY: str = Field(default="")
    OPENAI_API_BASE: str = Field(default="https://api.openai.com")
    OPENAI_MODEL: str = Field(default="gpt-4o-mini")
    OPENAI_ORG: str = Field(default="", env="OPENAI_ORG")            # ← добавить
    OPENAI_PROJECT: str = Field(default="", env="OPENAI_PROJECT")

    # Telegram
    BOT_TOKEN: str = Field(default="")

    # SuitePro
    SUITEPRO_API_URL: str = Field(default="https://api.suitepro.to")
    SUITEPRO_API_KEY: str = Field(default="")

    # Явные эндпойнты (можешь переопределить в .env)
    SUITEPRO_CATEGORIES_URL: str | None = None
    SUITEPRO_METADATA_URL: str | None = None
    SUITEPRO_CLASSIFIEDS_URL: str | None = None
    SUITEPRO_ACCOUNTS_URL: str | None = None

    # SMS-Activate
    SMS_ACTIVATE_URL: str = Field(default="https://api.sms-activate.ae/stubs/handler_api.php")
    SMS_ACTIVATE_API_KEY: str = Field(default="")

    # HTTP
    API_TIMEOUT: int = Field(default=120)   # секунды
    API_RETRIES: int = Field(default=3)

    # Email чекер (оставим для других частей кода)
    EMAIL_CHECK_INTERVAL: int = Field(default=2)
    EMAIL_OVERALL_TIMEOUT: int = Field(default=180)

    # --- Регистрация / таймауты / воркеры (нужно для registration_bot.py) ---
    REG_MAX_WAIT_SMS: int = Field(180, env="REG_MAX_WAIT_SMS")
    REG_EMAIL_CHECK_INTERVAL: int = Field(5, env="REG_EMAIL_CHECK_INTERVAL")
    REG_MAX_THREADS: int = Field(3, env="REG_MAX_THREADS")
    REG_MAX_IMAP_WORKERS: int = Field(5, env="REG_MAX_IMAP_WORKERS")
    REG_CONNECTION_TIMEOUT: int = Field(15, env="REG_CONNECTION_TIMEOUT")
    REG_LOGIN_TIMEOUT: int = Field(30, env="REG_LOGIN_TIMEOUT")

    # --- Прокси (если включишь REG_USE_PROXY=true) ---
    REG_USE_PROXY: bool = Field(False, env="REG_USE_PROXY")
    REG_SOCKS5_HOST: str | None = Field(None, env="REG_SOCKS5_HOST")
    REG_SOCKS5_PORT: int = Field(0, env="REG_SOCKS5_PORT")  # 0 чтобы int(...) не падал
    REG_SOCKS5_USER_TEMPLATE: str | None = Field(None, env="REG_SOCKS5_USER_TEMPLATE")
    REG_SOCKS5_PASSWORD: str | None = Field(None, env="REG_SOCKS5_PASSWORD")

    # Регистрационный троттлинг (оставляю как у тебя)
    REG_BATCH_SIZE: int = Field(default=2)
    REG_BATCH_PAUSE_SEC: int = Field(default=11 * 60)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Алиасы/вычисляемые свойства ---
    # registration_bot.py ожидает SMS_ACTIVATE_KEY
    @property
    def SMS_ACTIVATE_KEY(self) -> str:
        return self.SMS_ACTIVATE_API_KEY

    @property
    def categories_url(self) -> str:
        return self.SUITEPRO_CATEGORIES_URL or f"{self.SUITEPRO_API_URL.rstrip('/')}/categories"

    @property
    def metadata_url(self) -> str:
        return self.SUITEPRO_METADATA_URL or f"{self.SUITEPRO_API_URL.rstrip('/')}/metadata"

    @property
    def classifieds_url(self) -> str:
        return self.SUITEPRO_CLASSIFIEDS_URL or f"{self.SUITEPRO_API_URL.rstrip('/')}/classifieds"

    @property
    def accounts_url(self) -> str:
        return self.SUITEPRO_ACCOUNTS_URL or f"{self.SUITEPRO_API_URL.rstrip('/')}/accounts/"


@lru_cache(1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
