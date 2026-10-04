"""Testes da escolha automática de gráfico. Não usam banco nem LLM."""

from cinedata_agent.charts import build_chart, plan_chart
from cinedata_agent.db import QueryResult
from cinedata_agent.formatting import format_number


def result(columns, rows):
    return QueryResult(columns=list(columns), rows=[tuple(r) for r in rows], truncated=False, elapsed_seconds=0)


TOP_RECEITA = result(
    ["titulo", "ano_lancamento", "receita_brl"],
    [("Avatar", 2022, 12_390_136_500.54), ("Endgame", 2019, 11_094_720_000), ("No Way Home", 2021, 10_977_782_882.74)],
)


def test_ranking_de_filmes_vira_barras_com_ano_e_escala_em_bilhoes():
    plan = plan_chart(TOP_RECEITA)
    assert plan.kind == "bar"
    assert plan.labels == ["Avatar (2022)", "Endgame (2019)", "No Way Home (2021)"]
    assert round(plan.values[0], 2) == 12.39
    assert plan.axis_title == "Receita (R$ bi)"
    assert plan.tooltips[0] == "R$ 12,39 bi"


def test_contagem_e_a_metrica_quando_e_a_unica():
    plan = plan_chart(result(["genero", "qtd_filmes"], [("Drama", 28086), ("Documentário", 18082), ("Comédia", 16048)]))
    assert plan.kind == "bar" and plan.values == [28086, 18082, 16048]
    assert plan.tooltips == ["28.086", "18.082", "16.048"]


def test_contagem_de_apoio_nao_concorre_com_a_metrica():
    plan = plan_chart(result(["genero", "margem_media", "qtd_filmes"], [("Terror", 0.76, 148), ("Aventura", 0.696, 238), ("Animação", 0.694, 90)]))
    assert plan.axis_title == "Margem media (%)"
    assert round(plan.values[0], 1) == 76.0


def test_serie_por_ano_vira_linha():
    plan = plan_chart(result(["ano_lancamento", "nota_media_imdb"], [(2016, 6.338), (2017, 6.339), (2018, 6.272)]))
    assert plan.kind == "line" and plan.labels == ["2016", "2017", "2018"]
    assert plan.tooltips[0] == "6,34"


def test_dupla_de_textos_vira_um_rotulo_so():
    plan = plan_chart(result(["ator", "diretor", "qtd_filmes"], [("A", "B", 37), ("C", "B", 32), ("D", "E", 31)]))
    assert plan.labels[0] == "A · B"


def test_titulos_repetidos_ganham_rotulos_unicos():
    plan = plan_chart(result(["titulo", "qtd"], [("Die Hart", 13), ("Die Hart", 12), ("Outro", 11)]))
    assert plan.labels == ["Die Hart", "Die Hart (2)", "Outro"]


def test_sem_grafico_quando_nao_ajuda():
    assert plan_chart(result(["nome", "qtd"], [("Eric Roberts", 105)])) is None  # 1 linha: o texto basta
    assert plan_chart(result(["titulo", "nota_tmdb", "nota_imdb"], [("A", 8.1, 1.7), ("B", 8.5, 2.2), ("C", 6.2, 2.2)])) is None
    assert plan_chart(result(["titulo", "nota"], [(f"F{i}", i) for i in range(30)])) is None  # muitas linhas
    assert plan_chart(result(["titulo", "receita_brl"], [("A", 1.0), ("B", None), ("C", 3.0)])) is None


def test_graficos_sao_construidos():
    assert build_chart(plan_chart(TOP_RECEITA), "dark").to_dict()["mark"]["type"] == "bar"
    line = plan_chart(result(["ano_lancamento", "nota_media_imdb"], [(2016, 6.3), (2017, 6.4), (2018, 6.2)]))
    assert build_chart(line).to_dict()["mark"]["type"] == "line"


def test_numero_no_padrao_brasileiro():
    assert format_number(2994.357) == "2.994,36"
    assert format_number(28086) == "28.086"
