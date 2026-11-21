from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-sonnet-4-6", alias="CLAUDE_MODEL")

    bezala_username: str = Field(default="", alias="BEZALA_USERNAME")
    bezala_password: str = Field(default="", alias="BEZALA_PASSWORD")
    bezala_api_url: str = Field(default="https://app.bezala.com/api", alias="BEZALA_API_URL")

    gmail_client_id: str = Field(default="", alias="GMAIL_CLIENT_ID")
    gmail_client_secret: str = Field(default="", alias="GMAIL_CLIENT_SECRET")
    gmail_refresh_token: str = Field(default="", alias="GMAIL_REFRESH_TOKEN")
    drive_refresh_token: str = Field(default="", alias="DRIVE_REFRESH_TOKEN")

    # C45 — privat Gmail (bara gmail.readonly). Boten läser BARA mail från
    # dessa avsändare (kommaseparerad lista, exakta adresser). Allt annat
    # i den privata brevlådan hämtas, lagras, loggas eller analyseras aldrig.
    private_gmail_sender_allowlist: str = Field(
        default="no_reply@email.apple.com",
        alias="PRIVATE_GMAIL_SENDER_ALLOWLIST",
    )

    google_drive_folder_id: str = Field(
        default="1FoK-nmaDLgIUnMUImECjxXBO9XqLgFZb",
        alias="GOOGLE_DRIVE_FOLDER_ID",
    )

    database_url: str = Field(default="sqlite:///./bezalabot.db", alias="DATABASE_URL")

    scan_interval_minutes: int = Field(default=60, alias="SCAN_INTERVAL_MINUTES")
    scan_enabled: bool = Field(default=True, alias="SCAN_ENABLED")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    app_password: str = Field(default="", alias="APP_PASSWORD")
    session_secret: str = Field(default="", alias="SESSION_SECRET")


@lru_cache
def get_settings() -> Settings:
    return Settings()
