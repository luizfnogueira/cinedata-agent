"""Testes da camada semântica e do prompt. Rodam direto no banco, sem chamar LLM."""

import sqlite3

import pytest

from cinedata_agent.config import PROJECT_ROOT
from cinedata_agent.prompts import FEW_SHOT_SQL, build_system_prompt
from cinedata_agent.semantic import GENEROS_PT, create_semantic_views, reference_year

DB_PATH = PROJECT_ROOT / "cinerocket.db"

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="cinerocket.db não encontrado")


@pytest.fixture(scope="module")
def conn():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.execute("PRAGMA cache_size=-262144")
    create_semantic_views(conn)
    yield conn
    conn.close()


def scalar(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_lucro_so_existe_com_receita_e_orcamento(conn):
    assert scalar(conn, """
        SELECT COUNT(*) FROM vw_filmes
        WHERE lucro_usd IS NOT NULL AND (receita_usd IS NULL OR orcamento_usd IS NULL)
    """) == 0
    assert scalar(conn, "SELECT COUNT(lucro_usd) FROM vw_filmes") > 0


def test_margem_ignora_valores_irrisorios(conn):
    assert scalar(conn, """
        SELECT COUNT(*) FROM vw_filmes
        WHERE margem_lucro IS NOT NULL AND (receita_usd < 10000 OR orcamento_usd < 10000)
    """) == 0
    assert scalar(conn, "SELECT MAX(margem_lucro) FROM vw_filmes") <= 1


def test_nota_tmdb_sem_votos_vira_nulo(conn):
    assert scalar(conn, "SELECT COUNT(*) FROM vw_filmes WHERE nota_tmdb IS NOT NULL AND NOT qtd_tmdb > 0") == 0


def test_popularidade_contaminada_com_ano_vira_nula(conn):
    top = [row[0] for row in conn.execute("SELECT popularidade FROM vw_filmes ORDER BY popularidade DESC LIMIT 10")]
    assert not any(1900 <= p <= 2030 and p == int(p) for p in top)


def test_view_de_filmes_tem_uma_linha_por_filme(conn):
    assert scalar(conn, "SELECT COUNT(*) FROM vw_filmes") == scalar(conn, "SELECT COUNT(*) FROM dim_movies")


def test_todos_generos_tem_traducao(conn):
    generos = {row[0] for row in conn.execute("SELECT nome_genero FROM dim_genres")}
    assert generos == set(GENEROS_PT)


def test_ano_de_referencia_ignora_anos_quase_vazios(conn):
    assert reference_year(conn) == 2024


def test_banco_continua_somente_leitura(conn):
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM dim_genres")


@pytest.mark.parametrize("sql", FEW_SHOT_SQL)
def test_exemplos_do_prompt_executam(conn, sql):
    assert conn.execute(sql).fetchall()


def test_prompt_preenche_placeholders():
    prompt = build_system_prompt(2024)
    assert "{" not in prompt and "}" not in prompt
    assert "BETWEEN 2020 AND 2024" in prompt
    assert "US$ 10.000" in prompt
