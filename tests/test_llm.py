"""Testes do timeout por requisição. Usam um modelo falso: não chamam a API."""

import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cinedata_agent.llm import ModelTimeoutError, TimeoutModel


def model_that_takes(seconds: float) -> FunctionModel:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(seconds)
        return ModelResponse(parts=[TextPart("ok")])

    return FunctionModel(respond)


def test_resposta_rapida_passa():
    agent = Agent(TimeoutModel(model_that_takes(0), timeout_seconds=1))
    assert agent.run_sync("oi").output == "ok"


def test_requisicao_lenta_e_interrompida():
    agent = Agent(TimeoutModel(model_that_takes(5), timeout_seconds=0.2))
    with pytest.raises(ModelTimeoutError, match="não respondeu"):
        agent.run_sync("oi")
