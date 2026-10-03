"""Testes do cache de respostas. Não usam o banco do catálogo nem LLM."""

from cinedata_agent.agent import AgentAnswer, ExecutedQuery
from cinedata_agent.cache import AnswerCache, normalize_question
from cinedata_agent.db import QueryResult


def make_answer(question: str = "Top 3 filmes por receita?") -> AgentAnswer:
    result = QueryResult(
        columns=["titulo", "receita_brl"],
        rows=[("Avatar", 12.5), ("Endgame", 11.1)],
        truncated=False,
        elapsed_seconds=0.01,
    )
    return AgentAnswer(
        question=question,
        answer="Avatar lidera.",
        queries=[ExecutedQuery(sql="SELECT x", error="no such column"), ExecutedQuery(sql="SELECT titulo", result=result)],
        model_name="qwen/qwen3.8-27b:free",
        requests=3,
        messages=[],
    )


def test_normalizacao_ignora_maiusculas_acentos_e_pontuacao():
    assert normalize_question("  Qual o LUCRO médio por gênero?? ") == normalize_question("qual o lucro medio por genero")
    assert normalize_question("Receita em R$") == "receita em r$"


def test_guarda_e_recupera_resposta_completa(tmp_path):
    cache = AnswerCache(tmp_path / "c.db", fingerprint="v1")
    cache.put(make_answer())

    hit = cache.get("top 3 filmes por receita")
    assert hit is not None
    assert hit.cached and hit.requests == 0
    assert hit.answer == "Avatar lidera."
    assert hit.model_name == "qwen/qwen3.8-27b:free"
    assert hit.queries[0].error == "no such column"
    assert hit.final_query.result.rows == [("Avatar", 12.5), ("Endgame", 11.1)]


def test_pergunta_diferente_nao_acerta_o_cache(tmp_path):
    cache = AnswerCache(tmp_path / "c.db", fingerprint="v1")
    cache.put(make_answer())
    assert cache.get("Top 5 filmes por receita?") is None


def test_mudanca_de_prompt_ou_modelo_invalida_o_cache(tmp_path):
    AnswerCache(tmp_path / "c.db", fingerprint="prompt-v1").put(make_answer())
    assert AnswerCache(tmp_path / "c.db", fingerprint="prompt-v2").get(make_answer().question) is None


def test_limpar_cache(tmp_path):
    cache = AnswerCache(tmp_path / "c.db", fingerprint="v1")
    cache.put(make_answer("a"))
    cache.put(make_answer("b"))
    assert cache.clear() == 2
    assert cache.get("a") is None
