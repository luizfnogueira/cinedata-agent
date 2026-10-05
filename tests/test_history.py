"""Testes do histórico de conversas. Não usam o banco do catálogo nem LLM."""

from cinedata_agent.agent import AgentAnswer, ExecutedQuery
from cinedata_agent.db import QueryResult
from cinedata_agent.history import ConversationStore, make_title


def make_answer(question: str) -> AgentAnswer:
    result = QueryResult(columns=["titulo", "receita_brl"], rows=[("Avatar", 12.5)], truncated=False, elapsed_seconds=0.01)
    return AgentAnswer(
        question=question,
        answer="Avatar lidera.",
        queries=[ExecutedQuery(sql="SELECT titulo, receita_brl FROM vw_filmes", result=result)],
        model_name="m",
        requests=2,
        messages=[],
    )


def test_titulo_curto_fica_inteiro_e_longo_e_cortado():
    assert make_title("  Top 10   filmes ") == "Top 10 filmes"
    long_title = make_title("Quais são os 10 filmes com maior margem de lucro, entre os que possuem receita e orçamento?")
    assert len(long_title) <= 48 and long_title.endswith("…")


def test_cria_conversa_e_guarda_perguntas_e_respostas(tmp_path):
    store = ConversationStore(tmp_path / "c.db")
    cid = store.create("Top 10 filmes por receita")
    store.add_turn(cid, "Top 10 filmes por receita", make_answer("Top 10"), None)
    store.add_turn(cid, "Pergunta que falhou", None, "O serviço de IA está sobrecarregado.")

    turns = store.turns(cid)
    assert [t.question for t in turns] == ["Top 10 filmes por receita", "Pergunta que falhou"]
    assert turns[0].answer.answer == "Avatar lidera."
    assert turns[0].answer.final_query.result.rows == [("Avatar", 12.5)]
    assert turns[1].answer is None and "sobrecarregado" in turns[1].error


def test_lista_fixadas_primeiro_e_depois_as_mais_recentes(tmp_path):
    store = ConversationStore(tmp_path / "c.db")
    first = store.create("primeira")
    second = store.create("segunda")
    third = store.create("terceira")
    store.add_turn(first, "primeira", None, "x")  # "primeira" vira a mais recente
    store.set_pinned(third, True)

    assert [c.id for c in store.recent()] == [third, first, second]
    assert store.recent()[0].pinned

    store.set_pinned(third, False)
    assert not store.recent()[-1].pinned


def test_excluir_apaga_a_conversa_e_as_mensagens(tmp_path):
    store = ConversationStore(tmp_path / "c.db")
    cid = store.create("apagar")
    store.add_turn(cid, "apagar", make_answer("apagar"), None)
    store.delete(cid)

    assert not store.exists(cid)
    assert store.recent() == []
    assert store.turns(cid) == []


def test_historico_sobrevive_a_reabrir_o_arquivo(tmp_path):
    cid = ConversationStore(tmp_path / "c.db").create("persistente")
    assert ConversationStore(tmp_path / "c.db").exists(cid)
