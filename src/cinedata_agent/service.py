"""Fachada da aplicação: junta banco, modelo, agente e cache.

A CLI e a interface Streamlit usam só esta classe, sem montar as peças por conta própria.
"""

import asyncio
import warnings

from openai import AsyncOpenAI
from pydantic_ai.models import Model

from cinedata_agent.agent import AgentAnswer, ask, create_agent
from cinedata_agent.cache import AnswerCache, cache_fingerprint
from cinedata_agent.config import Settings
from cinedata_agent.db import Database
from cinedata_agent.llm import build_fallback_model, build_model, build_provider
from cinedata_agent.prompts import build_system_prompt

CACHE_FILENAME = "respostas.db"


def is_cacheable(answer: AgentAnswer) -> bool:
    """Guarda respostas embasadas em consulta bem-sucedida, ou recusas sem consulta.

    Não guarda quando todas as consultas falharam: a próxima tentativa pode dar certo.
    """
    return answer.final_query is not None or not answer.queries


class CineDataService:
    def __init__(
        self,
        db: Database,
        model: Model,
        cache: AnswerCache | None = None,
        http_client: AsyncOpenAI | None = None,
    ) -> None:
        self.db = db
        self.model = model
        self.cache = cache
        self.agent = create_agent(model)
        self._http_client = http_client

    @classmethod
    def from_settings(cls, settings: Settings, *, model_name: str | None = None, use_cache: bool = True) -> "CineDataService":
        """Monta o serviço. Sem `model_name`, usa a cadeia de fallback de settings.models."""
        db = Database.from_settings(settings)
        provider = build_provider(settings)
        model = build_model(settings, model_name, provider) if model_name else build_fallback_model(settings, provider)
        cache = None
        if use_cache:
            fingerprint = cache_fingerprint(build_system_prompt(db.reference_year), settings.db_path, model.model_name)
            cache = AnswerCache(settings.cache_dir / CACHE_FILENAME, fingerprint)
        return cls(db, model, cache, provider.client)

    def ask(self, question: str, *, use_cache: bool = True) -> AgentAnswer:
        """Responde a pergunta, consultando o cache antes de gastar requisições."""
        if use_cache and self.cache and (cached := self.cache.get(question)):
            return cached
        answer = ask(self.agent, question, self.db)
        if self.cache and is_cacheable(answer):
            self.cache.put(answer)
        return answer

    def close(self) -> None:
        """Fecha o cliente HTTP e o banco explicitamente.

        Deixar as conexões HTTPS assíncronas e a conexão SQLite (que tem callbacks
        Python) para a finalização do interpretador causou um segmentation fault ao
        fim de uma avaliação com chamadas reais ao modelo.
        """
        if self._http_client is not None:
            # O run_sync do PydanticAI usa o event loop corrente da thread principal:
            # o cliente precisa ser fechado nesse mesmo loop.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.run_until_complete(self._http_client.close())
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
            self._http_client = None
        self.db.close()

    def __enter__(self) -> "CineDataService":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
