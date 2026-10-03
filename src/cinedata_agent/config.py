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

    # Ordem importa: o primeiro é o principal, os demais são fallback, ordenados por tempo
    # de resposta observado. Modelos de provedores diferentes não dividem o mesmo pool.
    # - Qwen: o mais rápido e consistente (4–10 s por pergunta) e o que melhor segue as regras.
    # - Gemma: quando lotado, devolve 429 na hora, então falhar nele custa ~0 s.
    # - Nemotron: por último. Travou na fila em 3 de 6 chamadas (cada trava custa o timeout
    #   inteiro) e errou a escala de um valor (R$ 1,0 mi apresentado como R$ 1,0 mil).
    models: list[str] = Field(
        default=[
            "qwen/qwen3.8-27b:free",
            "google/gemma-4-26b-a4b-it:free",
            "nvidia/nemotron-3.5-lightning:free",
        ],
        validation_alias="CINEDATA_MODELS",
    )

    db_path: Path = Field(default=PROJECT_ROOT / "cinerocket.db", validation_alias="CINEDATA_DB_PATH")
    cache_dir: Path = Field(default=PROJECT_ROOT / ".cache", validation_alias="CINEDATA_CACHE_DIR")

    # Limites de execução das consultas geradas pelo agente
    max_rows: int = 50
    query_timeout_seconds: float = 10.0

    # Tempo máximo de relógio por requisição ao modelo. Respostas normais levaram de 2 a 10 s;
    # acima disso o pedido costuma estar parado na fila do pool gratuito, e é melhor
    # desistir logo e passar para o próximo modelo.
    model_timeout_seconds: float = 45.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
