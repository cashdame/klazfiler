from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Telegram
    BOT_TOKEN: str = Field(..., alias="BOT_TOKEN")

    # SuitePro API (фиксированный базовый URL)
    SUITEPRO_API_URL: str = "https://api.suitepro.to"
    SUITEPRO_API_KEY: str = ""

    # SMS-Activate
    SMS_ACTIVATE_URL: str = "https://api.sms-activate.ae/stubs/handler_api.php"
    SMS_ACTIVATE_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("SMS_ACTIVATE_KEY", "SMS_ACTIVATE_API_KEY"),
    )

    # Архив граббера
    ARCHIVE_DIR: str = "/opt/klazfiler/archive"

    # HTTP
    API_TIMEOUT: int = 120
    API_RETRIES: int = 3

    # --- Регистрация ---
    # потоков и паузы можно оставить как есть
    REG_MAX_WAIT_SMS: int = 60
    REG_EMAIL_CHECK_INTERVAL: int = 5
    REG_MAX_THREADS: int = 10
    REG_MAX_IMAP_WORKERS: int = 10
    REG_CONNECTION_TIMEOUT: int = 30
    REG_LOGIN_TIMEOUT: int = 30

    # SOCKS5 (опционально)
    REG_USE_PROXY: bool = False
    REG_SOCKS5_HOST: str = "eu.proxys5.net"
    REG_SOCKS5_PORT: int = 6200
    REG_SOCKS5_USER_TEMPLATE: str = "{session_id}"
    REG_SOCKS5_PASSWORD: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

settings = Settings()
