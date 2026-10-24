from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gmail_address: str = ""
    gmail_app_password: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    use_llm_parser: bool = True
    use_llm_composer: bool = True
    llm_timeout_seconds: float = 30.0

    uarb_url: str = "https://uarb.novascotia.ca/fmi/webd/UARB15"
    poll_interval_seconds: int = 30
    max_documents: int = 10
    max_jobs_per_email: int = 5
    max_attachment_bytes: int = 20 * 1024 * 1024
    headless: bool = True
    download_dir: Path = Path("downloads")


settings = Settings()
