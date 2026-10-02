"""Configurações da aplicação, lidas de variáveis de ambiente / arquivo .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrouter_api_key: SecretStr = Field(validation_alias="OPENROUTER_API_KEY")

    # Ordem importa: o primeiro é o principal, os demais são fallback.
    # Modelos de provedores diferentes não compartilham o mesmo pool de capacidade.
    models: list[str] = Field(
        default=[
            "nvidia/nemotron-3.5-lightning:free",
            "google/gemma-4-26b-a4b-it:free",
            "qwen/qwen3.8-27b:free",
        ],
        validation_alias="CINEDATA_MODELS",
    )

    db_path: Path = Field(default=PROJECT_ROOT / "cinerocket.db", validation_alias="CINEDATA_DB_PATH")
    cache_dir: Path = Field(default=PROJECT_ROOT / ".cache", validation_alias="CINEDATA_CACHE_DIR")

    # Limites de execução das consultas geradas pelo agente
    max_rows: int = 50
    query_timeout_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
