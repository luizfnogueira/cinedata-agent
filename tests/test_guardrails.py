"""Testes dos guardrails de SQL. Não usam banco nem LLM."""

import pytest

from cinedata_agent.guardrails import UnsafeQueryError, validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT titulo FROM vw_filmes LIMIT 5",
        "select titulo from vw_filmes limit 5;",
        "WITH top AS (SELECT * FROM vw_filmes ORDER BY receita_brl DESC LIMIT 10) SELECT titulo FROM top",
        "SELECT g.genero, COUNT(*) FROM vw_filmes f JOIN vw_filme_genero g USING (sk_movie_id) GROUP BY 1",
        "SELECT m.titulo FROM dim_movies m JOIN fact_movies_performance f ON f.sk_movie_id = m.sk_movie_id",
        "SELECT titulo FROM vw_filmes UNION SELECT nome_pessoa FROM vw_filme_pessoa",
        "SELECT * FROM (SELECT titulo, ano_lancamento FROM vw_filmes) AS s WHERE s.ano_lancamento = 2020",
        # Texto perigoso dentro de string ou comentário não é comando
        "SELECT titulo FROM vw_filmes WHERE titulo = '; DROP TABLE dim_movies'",
        "SELECT titulo FROM vw_filmes -- ; DELETE FROM dim_movies",
    ],
)
def test_aceita_consultas_de_leitura(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM dim_movies",
        "DROP TABLE dim_movies",
        "UPDATE fact_movies_performance SET receita_brl = 0",
        "INSERT INTO dim_genres VALUES ('x', 'y')",
        "CREATE TABLE x (a INT)",
        "ALTER TABLE dim_movies ADD COLUMN x INT",
        "PRAGMA table_info(dim_movies)",
        "ATTACH DATABASE 'outro.db' AS outro",
        "VACUUM",
        "SELECT 1; DROP TABLE dim_movies",
        "SELECT titulo FROM vw_filmes; SELECT titulo FROM vw_filmes",
        "SELECT load_extension('malicioso')",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM tabela_que_nao_existe",
        "",
        "   ;  ",
        "SELECT * FROM vw_filmes WHERE (",
    ],
)
def test_recusa_consultas_perigosas_ou_invalidas(sql):
    with pytest.raises(UnsafeQueryError):
        validate_sql(sql)


def test_remove_ponto_e_virgula_final():
    assert validate_sql("SELECT 1 FROM vw_filmes;  ") == "SELECT 1 FROM vw_filmes"


def test_recusa_consulta_muito_longa():
    with pytest.raises(UnsafeQueryError, match="caracteres"):
        validate_sql("SELECT titulo FROM vw_filmes WHERE " + " OR ".join(["1 = 1"] * 2_000))


def test_mensagem_de_tabela_invalida_sugere_as_views():
    with pytest.raises(UnsafeQueryError, match="vw_filmes"):
        validate_sql("SELECT * FROM filmes")
