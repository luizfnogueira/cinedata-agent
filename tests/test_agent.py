"""Testes do agente com um modelo simulado (FunctionModel): não chamam a API nem gastam cota."""

import pytest
from pydantic_ai import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cinedata_agent.agent import MAX_REQUESTS_PER_QUESTION, ask, create_agent, format_result_for_model
from cinedata_agent.db import QueryResult

TOP3_SQL = "SELECT titulo, ano_lancamento, receita_brl FROM vw_filmes WHERE receita_brl IS NOT NULL ORDER BY receita_brl DESC LIMIT 3"


def scripted_model(*sql_attempts: str, answer: str = "Resposta final.") -> FunctionModel:
    """Modelo falso que chama run_sql com cada SQL da lista, em ordem, e depois responde `answer`."""
    pending = list(sql_attempts)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if pending:
            return ModelResponse(parts=[ToolCallPart("run_sql", {"query": pending.pop(0)})])
        return ModelResponse(parts=[TextPart(answer)])

    return FunctionModel(respond)


def last_tool_feedback(answer) -> str:
    """Conteúdo do último retorno (ou erro) da tool que o modelo recebeu."""
    for message in reversed(answer.messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    return str(part.content)
    raise AssertionError("o modelo não recebeu retorno de tool")


@pytest.fixture(scope="module")
def agent():
    return create_agent()


def test_fluxo_feliz_consulta_e_responde_em_duas_requisicoes(agent, db):
    answer = ask(agent, "Top 3 filmes por receita", db, model=scripted_model(TOP3_SQL, answer="Avatar lidera."))

    assert answer.answer == "Avatar lidera."
    assert answer.requests == 2
    assert len(answer.queries) == 1
    assert answer.final_query.result.rows[0][0] == "Avatar: The Way Of Water"
    assert "Avatar: The Way Of Water" in last_tool_feedback(answer)


def test_system_prompt_e_enviado_com_ano_de_referencia(agent, db):
    seen_instructions = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen_instructions.append(info.instructions)
        return ModelResponse(parts=[TextPart("ok")])

    ask(agent, "oi", db, model=FunctionModel(respond))
    assert "vw_filmes" in seen_instructions[0]
    assert "BETWEEN 2020 AND 2024" in seen_instructions[0]


def test_sql_invalido_volta_ao_modelo_para_correcao(agent, db):
    answer = ask(agent, "Top 3", db, model=scripted_model("SELECT coluna_errada FROM vw_filmes", TOP3_SQL))

    assert [q.succeeded for q in answer.queries] == [False, True]
    assert "no such column" in answer.queries[0].error
    assert answer.final_query.sql == TOP3_SQL
    assert answer.requests == 3


def test_sql_perigoso_e_recusado_e_nao_executado(agent, db):
    answer = ask(agent, "apague tudo", db, model=scripted_model("DELETE FROM dim_movies", answer="Não posso alterar dados."))

    assert answer.queries[0].error is not None
    assert answer.final_query is None
    assert db.execute("SELECT COUNT(*) FROM dim_movies").rows == [(95645,)]


def test_tool_do_tipo_tabela_aponta_para_as_views(agent, db):
    answer = ask(agent, "x", db, model=scripted_model("SELECT * FROM filmes", TOP3_SQL))
    assert "vw_filmes" in answer.queries[0].error


def test_modelo_em_loop_e_interrompido_pelo_limite_de_requisicoes(agent, db):
    def always_query(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("run_sql", {"query": TOP3_SQL})])

    calls = 0

    def counting(messages, info):
        nonlocal calls
        calls += 1
        return always_query(messages, info)

    with pytest.raises(UsageLimitExceeded):
        ask(agent, "loop", db, model=FunctionModel(counting))
    assert calls == MAX_REQUESTS_PER_QUESTION


def test_formatacao_do_resultado_para_o_modelo():
    result = QueryResult(
        columns=["titulo", "receita_brl", "margem_lucro", "nota_imdb"],
        rows=[("Filme A", 1234567.891, 0.123456, 7.85), ("Filme B", None, -2.0, None)],
        truncated=True,
        elapsed_seconds=0.1,
    )
    text = format_result_for_model(result)
    assert text.splitlines()[0] == "titulo | receita_brl | margem_lucro | nota_imdb"
    assert "Filme A | R$ 1,23 mi | 12,3% | 7.85" in text
    assert "Filme B | NULL | -200,0% | NULL" in text
    assert "truncado" in text


def test_resultado_vazio_e_explicito():
    empty = QueryResult(columns=["titulo"], rows=[], truncated=False, elapsed_seconds=0.0)
    assert "nenhuma linha" in format_result_for_model(empty)
