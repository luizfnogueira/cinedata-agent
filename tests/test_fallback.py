"""Testes da cadeia de fallback entre modelos. Usam modelos falsos: não chamam a API."""

import asyncio

import pytest
from pydantic_ai import Agent, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cinedata_agent.llm import ModelTimeoutError, TimeoutModel, should_fallback

PROVIDER_BUSY = ModelHTTPError(429, "gemma", {"message": "Provider returned error", "metadata": {"raw": "rate-limited upstream"}})
DAILY_QUOTA = ModelHTTPError(429, "qwen", {"message": "Rate limit exceeded: free-models-per-day. Add 10 credits..."})


def failing_model(error: Exception, calls: list[str], name: str) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(name)
        raise error

    return FunctionModel(respond, model_name=name)


def answering_model(calls: list[str], name: str, delay: float = 0) -> FunctionModel:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(name)
        await asyncio.sleep(delay)
        return ModelResponse(parts=[TextPart(f"resposta do {name}")])

    return FunctionModel(respond, model_name=name)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PROVIDER_BUSY, True),
        (ModelHTTPError(500, "m"), True),
        (ModelHTTPError(503, "m"), True),
        (ModelTimeoutError("m", "não respondeu"), True),
        (DAILY_QUOTA, False),
        (ModelHTTPError(401, "m", {"message": "No auth credentials found"}), False),
        (ModelHTTPError(402, "m"), False),
        (ValueError("bug no nosso código"), False),
    ],
)
def test_politica_de_fallback(error, expected):
    assert should_fallback(error) is expected


def test_provedor_lotado_passa_para_o_proximo_modelo():
    calls: list[str] = []
    model = FallbackModel(
        failing_model(PROVIDER_BUSY, calls, "qwen"),
        answering_model(calls, "gemma"),
        fallback_on=should_fallback,
    )
    assert Agent(model).run_sync("oi").output == "resposta do gemma"
    assert calls == ["qwen", "gemma"]


def test_cota_diaria_esgotada_nao_tenta_outros_modelos():
    calls: list[str] = []
    model = FallbackModel(
        failing_model(DAILY_QUOTA, calls, "qwen"),
        answering_model(calls, "gemma"),
        fallback_on=should_fallback,
    )
    with pytest.raises(ModelHTTPError):
        Agent(model).run_sync("oi")
    assert calls == ["qwen"]


def test_modelo_travado_cai_no_timeout_e_passa_para_o_proximo():
    calls: list[str] = []
    model = FallbackModel(
        TimeoutModel(answering_model(calls, "nemotron", delay=5), timeout_seconds=0.2),
        answering_model(calls, "qwen"),
        fallback_on=should_fallback,
    )
    assert Agent(model).run_sync("oi").output == "resposta do qwen"
    assert calls == ["nemotron", "qwen"]
