"""Criação dos modelos do OpenRouter usados pelo agente."""

import asyncio

from openai import AsyncOpenAI
from pydantic_ai import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
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


def build_model(settings: Settings, model_name: str | None = None) -> Model:
    """Modelo principal (o primeiro da lista em settings.models), ou `model_name` se informado."""
    model = OpenRouterModel(model_name or settings.models[0], provider=build_provider(settings))
    return TimeoutModel(model, settings.model_timeout_seconds)
