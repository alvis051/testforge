from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TESTFORGE_", env_file=".env")

    database_url: str = "sqlite:///./testforge.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
