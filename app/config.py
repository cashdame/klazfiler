# app/config.py
from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Telegram
    BOT_TOKEN: str = Field(..., alias="BOT_TOKEN")

    # SuitePro
    SUITEPRO_API_URL: str = "https://api.suitepro.to"
    SUITEPRO_API_KEY: str
    SUITEPRO_CATEGORIES_URL: str = "https://api.suitepro.to/categories"
    SUITEPRO_METADATA_URL: str = "https://api.suitepro.to/metadata"
    SUITEPRO_CLASSIFIEDS_URL: str = "https://api.suitepro.to/classifieds/"

    # SMS-Activate
    SMS_ACTIVATE_URL: str = "https://api.sms-activate.ae/stubs/handler_api.php"
    # примет либо SMS_ACTIVATE_KEY, либо SMS_ACTIVATE_API_KEY
    SMS_ACTIVATE_KEY: str = Field(
        ...,
        validation_alias=AliasChoices("SMS_ACTIVATE_KEY", "SMS_ACTIVATE_API_KEY"),
    )

    # OpenAI
    OPENAI_API_KEY: str | None = None
    OPENAI_API_BASE: str | None = None
    OPENAI_MODEL: str | None = None

    # Paths
    ARCHIVE_DIR: str = "/opt/klazfiler/archive"

    # HTTP settings
    API_TIMEOUT: int = 120
    API_RETRIES: int = 3

    # Email check
    EMAIL_CHECK_INTERVAL: int = 2
    EMAIL_OVERALL_TIMEOUT: int = 180

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

def get_settings() -> Settings:
    return Settings()

# На уровне модуля можно сразу создать singleton
settings = get_settings()
