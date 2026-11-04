from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Non-credential configuration. Credentials never live here — they
    arrive per request via headers (see server.py), per SOP §3.1.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mcp_http_port: int = 8080
    mcp_http_host: str = "0.0.0.0"


def get_settings() -> Settings:
    return Settings()
