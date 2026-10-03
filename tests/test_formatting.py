"""Testes da formatação de dinheiro e margem. Não usam banco nem LLM."""

import pytest

from cinedata_agent.formatting import format_cell, format_money, format_percent


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (12_390_136_500.54, "R$ 12,39 bi"),
        (755_700_000, "R$ 755,70 mi"),
        # Caso real do smoke test: o Nemotron apresentou este valor como "R$ 1,0 mil"
        (-1_037_292.92, "-R$ 1,04 mi"),
        (450_909.26, "R$ 450,91 mil"),
        (999.5, "R$ 999,50"),
        (0, "R$ 0,00"),
    ],
)
def test_dinheiro_em_escala_legivel(value, expected):
    assert format_money(value) == expected


def test_dinheiro_em_dolar():
    assert format_money(2_000_000, "US$") == "US$ 2,00 mi"


def test_porcentagem_a_partir_de_fracao():
    assert format_percent(0.7604) == "76,0%"
    assert format_percent(-1749.0) == "-174.900,0%"


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [
        ("receita_brl", 1_234_567.891, "R$ 1,23 mi"),
        ("lucro_medio_brl", -1_037_292.92, "-R$ 1,04 mi"),
        ("receita_total_usd", 5_000_000_000, "US$ 5,00 bi"),
        ("margem_lucro", 0.25, "25,0%"),
        ("margem_media", -2.494, "-249,4%"),
        # Alias já em porcentagem: não multiplica de novo
        ("margem_pct", 76.0, "76.00"),
        ("nota_imdb", 7.85, "7.85"),
        ("divergencia", 0.123456, "0.1235"),
        ("ano_lancamento", 2019, "2019"),
        ("qtd_filmes", 134, "134"),
        ("titulo", "Avatar", "Avatar"),
        ("receita_brl", None, "NULL"),
    ],
)
def test_formatacao_por_nome_de_coluna(column, value, expected):
    assert format_cell(column, value) == expected
