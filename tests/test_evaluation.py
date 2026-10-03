"""Testes do avaliador: comparação de resultados e casos. Não chamam a API."""

import pytest

from cinedata_agent.agent import AgentAnswer, ExecutedQuery
from cinedata_agent.config import PROJECT_ROOT
from cinedata_agent.db import QueryResult
from cinedata_agent.evaluation import EvalCase, compare_results, judge, load_cases, markdown_report, run_case

CASES = load_cases(PROJECT_ROOT / "evals" / "casos.toml")


def result(columns, rows):
    return QueryResult(columns=list(columns), rows=[tuple(r) for r in rows], truncated=False, elapsed_seconds=0)


def case(**kwargs):
    return EvalCase(id="t", categoria="c", pergunta="p", **kwargs)


def answer(text="ok", query: QueryResult | None = None, sql="SELECT 1"):
    queries = [ExecutedQuery(sql=sql, result=query)] if query else []
    return AgentAnswer(question="p", answer=text, queries=queries, model_name="m", requests=2, messages=[])


GOLD_TOP = result(["titulo", "receita"], [("Avatar", 100.0), ("Endgame", 90.0), ("Spider", 80.0)])


# ── compare_results ─────────────────────────────────────────────────────


def test_aceita_colunas_extras_renomeadas_e_em_outra_ordem():
    agent = result(["ano", "receita_total_brl", "nome"], [(2022, 100.0, "Avatar"), (2019, 90.0, "Endgame"), (2021, 80.0, "Spider")])
    assert compare_results(case(ordenado=True), GOLD_TOP, agent) is None


def test_aceita_arredondamento():
    agent = result(["titulo", "receita"], [("Avatar", 100.2), ("Endgame", 90.1), ("Spider", 80.0)])
    assert compare_results(case(ordenado=True), GOLD_TOP, agent) is None


def test_recusa_valor_errado():
    agent = result(["titulo", "receita"], [("Avatar", 100.0), ("Endgame", 50.0), ("Spider", 80.0)])
    assert "receita" in compare_results(case(ordenado=True), GOLD_TOP, agent)


def test_recusa_quantidade_de_linhas_diferente():
    agent = result(["titulo", "receita"], [("Avatar", 100.0)])
    assert "1 linhas" in compare_results(case(), GOLD_TOP, agent)


def test_ordem_importa_em_ranking():
    agent = result(["titulo", "receita"], [("Endgame", 90.0), ("Avatar", 100.0), ("Spider", 80.0)])
    assert compare_results(case(ordenado=True), GOLD_TOP, agent) is not None
    assert compare_results(case(ordenado=False), GOLD_TOP, agent) is None


def test_empate_aceita_qualquer_ordem_dentro_do_bloco():
    gold = result(["titulo", "nota"], [("A", 9.0), ("B", 8.0), ("C", 8.0), ("D", 7.0)])
    agent = result(["titulo", "nota"], [("A", 9.0), ("C", 8.0), ("B", 8.0), ("D", 7.0)])
    assert compare_results(case(ordenado=True, ordem_por="nota"), gold, agent) is None


def test_ultimo_bloco_de_empate_aceita_outro_item_com_o_mesmo_valor():
    # O LIMIT cortou um empate em 7.0: "E" é tão correto quanto "D".
    gold = result(["titulo", "nota"], [("A", 9.0), ("B", 8.0), ("D", 7.0)])
    agent = result(["titulo", "nota"], [("A", 9.0), ("B", 8.0), ("E", 7.0)])
    assert compare_results(case(ordenado=True, ordem_por="nota"), gold, agent) is None


def test_valores_proximos_nao_contam_como_empate():
    gold = result(["titulo", "margem"], [("A", 0.9979), ("B", 0.9967), ("C", 0.9500)])
    agent = result(["titulo", "margem"], [("B", 0.9967), ("A", 0.9979), ("C", 0.9500)])
    assert compare_results(case(ordenado=True, ordem_por="margem"), gold, agent) is not None


def test_ordem_por_coluna_que_o_agente_nao_trouxe():
    gold = result(["titulo", "divergencia"], [("A", 6.4), ("B", 6.3), ("C", 4.0)])
    agent = result(["titulo", "nota_tmdb", "nota_imdb"], [("A", 8.1, 1.7), ("B", 8.5, 2.2), ("C", 6.2, 2.2)])
    assert compare_results(case(ordenado=True, ordem_por="divergencia", colunas=["titulo"]), gold, agent) is None


def test_apenas_primeira_linha():
    gold = result(["nome", "qtd"], [("Eric Roberts", 105)])
    agent = result(["nome", "qtd"], [("Eric Roberts", 105), ("Yogi Babu", 55)])
    assert compare_results(case(apenas_primeira=True), gold, agent) is None
    wrong = result(["nome", "qtd"], [("Yogi Babu", 55)])
    assert compare_results(case(apenas_primeira=True), gold, wrong) is not None


def test_contagem_inteira_exige_valor_exato():
    gold = result(["qtd"], [(105,)])
    assert compare_results(case(), gold, result(["n"], [(104,)])) is not None


# ── judge ───────────────────────────────────────────────────────────────


def test_recusa_correta_e_incorreta():
    refusal = case(tipo="recusa")
    assert judge(refusal, answer("Não posso alterar dados."), db=None) == ("acerto", "")
    status, reason = judge(refusal, answer(query=result(["x"], [(1,)])), db=None)
    assert status == "erro" and "executou SQL" in reason


def test_resposta_precisa_mencionar_o_texto_esperado(db):
    gold_case = case(sql="SELECT 'Eric Roberts' AS nome", contem=["Eric Roberts"])
    query = result(["nome"], [("Eric Roberts",)])
    assert judge(gold_case, answer("Quem mais atuou foi Eric Roberts.", query), db) == ("acerto", "")
    status, reason = judge(gold_case, answer("Foi o ator com mais filmes.", query), db)
    assert status == "erro" and "Eric Roberts" in reason


def test_falha_do_agente_vira_status_falha(db):
    def broken(question):
        raise RuntimeError("provedor fora do ar")

    r = run_case(case(sql="SELECT 1"), broken, db, describe_error=str)
    assert r.status == "falha" and r.motivo == "provedor fora do ar" and isinstance(r.error, RuntimeError)


# ── casos.toml ──────────────────────────────────────────────────────────


def test_casos_cobrem_todas_as_categorias_da_atividade():
    categories = {c.categoria for c in CASES}
    assert {
        "Bilheteria e Finanças",
        "Popularidade e Engajamento",
        "Elenco e Equipe",
        "Gêneros e Produtoras",
        "Avaliações dos Usuários",
    } <= categories


@pytest.mark.parametrize("eval_case", [c for c in CASES if c.tipo == "consulta"], ids=lambda c: c.id)
def test_gabarito_executa_e_tem_as_colunas_declaradas(db, eval_case):
    gold = db.execute(eval_case.sql)
    assert gold.rows
    for column in eval_case.colunas + ([eval_case.ordem_por] if eval_case.ordem_por else []):
        assert column in gold.columns
    # O gabarito comparado com ele mesmo tem que passar.
    assert compare_results(eval_case, gold, gold) is None


def test_relatorio_markdown(db):
    ok = run_case(case(sql="SELECT 1 AS x"), lambda q: answer(query=result(["x"], [(1,)])), db, str)
    text = markdown_report([ok], "agora")
    assert "Acertos: 1/1 (100%)" in text
