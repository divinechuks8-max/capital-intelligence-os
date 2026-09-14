from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, sourced from environment variables / .env.

    Never hold secrets here as literals — this class only reads them.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./dev.db"
    environment: str = "development"
    log_level: str = "INFO"
    sec_edgar_user_agent: str = ""
    companies_house_api_key: str = ""


settings = Settings()
