"""Criação dos modelos do OpenRouter usados pelo agente."""

import asyncio

from openai import AsyncOpenAI
from pydantic_ai import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.settings import ModelSettings

from cinedata_agent.config import Settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class ModelTimeoutError(ModelAPIError):
    """O modelo não respondeu dentro do tempo máximo por requisição."""


class TimeoutModel(WrapperModel):
    """Impõe um tempo máximo de relógio a cada requisição ao modelo.

    O timeout do cliente HTTP não basta: enquanto o pedido espera na fila de um
    modelo gratuito, o OpenRouter mantém a conexão viva, e já observamos
    requisições passando de 10 minutos. Como ModelTimeoutError é um ModelAPIError,
    o FallbackModel trata o estouro como falha e passa para o próximo modelo.
    """

    def __init__(self, wrapped: Model, timeout_seconds: float) -> None:
        super().__init__(wrapped)
        self.timeout_seconds = timeout_seconds

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await super().request(messages, model_settings, model_request_parameters)
        except TimeoutError as e:
            raise ModelTimeoutError(
                self.model_name, f"O modelo não respondeu em {self.timeout_seconds:.0f} s."
            ) from e


def build_provider(settings: Settings) -> OpenRouterProvider:
    # O cliente OpenAI repete por padrão até 2x em erros 429/5xx. No free tier
    # requisições que falham também contam na cota diária (50), então cada erro
    # custaria 3 requisições sem ninguém perceber. Desligamos o retry automático.
    client = AsyncOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=settings.openrouter_api_key.get_secret_value(),
        max_retries=0,
    )
    return OpenRouterProvider(openai_client=client)


def is_daily_quota_error(error: Exception) -> bool:
    """429 por cota diária esgotada (e não por provedor lotado)."""
    return isinstance(error, ModelHTTPError) and error.status_code == 429 and "per-day" in str(error.body).lower()


def should_fallback(error: Exception) -> bool:
    """Decide se vale tentar o próximo modelo.

    Troca em provedor lotado (429), erro do servidor (5xx), timeout e falha de
    conexão. Não troca quando todos os modelos falhariam igual (chave inválida,
    cota diária esgotada): cada tentativa extra seria uma requisição perdida.
    """
    if not isinstance(error, ModelAPIError):
        return False
    if isinstance(error, ModelHTTPError):
        if error.status_code in (401, 402, 403):
            return False
        if is_daily_quota_error(error):
            return False
    return True


def build_model(settings: Settings, model_name: str) -> Model:
    """Um único modelo do OpenRouter, com timeout por requisição."""
    model = OpenRouterModel(model_name, provider=build_provider(settings))
    return TimeoutModel(model, settings.model_timeout_seconds)


def build_fallback_model(settings: Settings) -> Model:
    """Todos os modelos de settings.models em cadeia: se um falhar, tenta o próximo, na ordem."""
    provider = build_provider(settings)
    models = [
        TimeoutModel(OpenRouterModel(name, provider=provider), settings.model_timeout_seconds)
        for name in settings.models
    ]
    if len(models) == 1:
        return models[0]
    return FallbackModel(*models, fallback_on=should_fallback)
