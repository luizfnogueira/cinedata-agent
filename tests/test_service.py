"""Testes da fachada CineDataService com modelo simulado: não chamam a API."""

import pytest
from openai import AsyncOpenAI
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cinedata_agent.cache import AnswerCache
from cinedata_agent.service import CineDataService

TOP3_SQL = "SELECT titulo, receita_brl FROM vw_filmes WHERE receita_brl IS NOT NULL ORDER BY receita_brl DESC LIMIT 3"


class ScriptedModel:
    """Conta as requisições. Chama run_sql com `sql` até receber um resultado e então responde."""

    def __init__(self, sql: str = TOP3_SQL) -> None:
        self.sql = sql
        self.requests = 0
        self.model = FunctionModel(self.respond)

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests += 1
        last_parts = messages[-1].parts
        got_result = any(getattr(p, "part_kind", "") == "tool-return" for p in last_parts)
        if not got_result:
            return ModelResponse(parts=[ToolCallPart("run_sql", {"query": self.sql})])
        return ModelResponse(parts=[TextPart("Avatar lidera.")])


@pytest.fixture
def scripted():
    return ScriptedModel()


@pytest.fixture
def service(db, scripted, tmp_path):
    return CineDataService(db, scripted.model, AnswerCache(tmp_path / "c.db", fingerprint="t"))


def test_pergunta_repetida_vem_do_cache_sem_requisicoes(service, scripted):
    first = service.ask("Top 3 filmes por receita?")
    second = service.ask("top 3 filmes por receita")

    assert not first.cached and first.requests == 2
    assert second.cached and second.requests == 0
    assert second.answer == first.answer
    assert scripted.requests == 2


def test_sem_cache_consulta_o_modelo_de_novo(service, scripted):
    service.ask("Top 3")
    service.ask("Top 3", use_cache=False)
    assert scripted.requests == 4


def test_falha_em_todas_as_consultas_nao_e_guardada(db, tmp_path):
    broken = ScriptedModel(sql="SELECT coluna_inexistente FROM vw_filmes")
    service = CineDataService(db, broken.model, AnswerCache(tmp_path / "c.db", fingerprint="t"))
    with pytest.raises(UnexpectedModelBehavior):
        service.ask("pergunta que falha")
    assert service.cache.get("pergunta que falha") is None


def test_funciona_sem_cache(db, scripted):
    service = CineDataService(db, scripted.model, cache=None)
    assert service.ask("Top 3").answer == "Avatar lidera."


class FakeDb:
    closed = False

    def close(self):
        self.closed = True


def test_close_fecha_cliente_http_e_banco():

    client = AsyncOpenAI(api_key="teste", base_url="https://openrouter.ai/api/v1")
    fake_db = FakeDb()
    with CineDataService(fake_db, ScriptedModel().model, http_client=client):
        pass
    assert fake_db.closed
    assert client.is_closed()
