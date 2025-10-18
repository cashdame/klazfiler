from __future__ import annotations
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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

    # SMS-Activate (для регистрации, оставил чтобы не ломать импорты)
    SMS_ACTIVATE_URL: str = Field(default="https://api.sms-activate.ae/stubs/handler_api.php")
    SMS_ACTIVATE_API_KEY: str = Field(default="")

    # HTTP
    API_TIMEOUT: int = Field(default=120)   # секунды
    API_RETRIES: int = Field(default=3)

    # Email чекер (если используешь)
    EMAIL_CHECK_INTERVAL: int = Field(default=2)
    EMAIL_OVERALL_TIMEOUT: int = Field(default=180)

    # Регистрационный троттлинг
    REG_BATCH_SIZE: int = Field(default=2)           # 2 регистрации
    REG_BATCH_PAUSE_SEC: int = Field(default=11*60)  # в 11 минут

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Вычисляемые урлы
    @property
    def categories_url(self) -> str:
        return self.SUITEPRO_CATEGORIES_URL or f"{self.SUITEPRO_API_URL.rstrip('/')}/categories"

    @property
    def metadata_url(self) -> str:
        return self.SUITEPRO_METADATA_URL or f"{self.SUITEPRO_API_URL.rstrip('/')}/metadata"

    @property
    def classifieds_url(self) -> str:
        # без финального слеша тоже ок
        return self.SUITEPRO_CLASSIFIEDS_URL or f"{self.SUITEPRO_API_URL.rstrip('/')}/classifieds"

    @property
    def accounts_url(self) -> str:
        # тут был NameError — теперь есть дефолт
        return self.SUITEPRO_ACCOUNTS_URL or f"{self.SUITEPRO_API_URL.rstrip('/')}/accounts/"


@lru_cache(1)
def get_settings() -> Settings:
    return Settings()


# короткий алиас
settings = get_settings()
