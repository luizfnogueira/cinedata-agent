"""Testes da conexão somente leitura. Rodam no banco real, sem LLM."""

import sqlite3

import pytest

from cinedata_agent.config import PROJECT_ROOT
from cinedata_agent.db import Database, QueryError, QueryTimeoutError
from cinedata_agent.guardrails import UnsafeQueryError

DB_PATH = PROJECT_ROOT / "cinerocket.db"

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="cinerocket.db não encontrado")


@pytest.fixture(scope="module")
def db():
    database = Database(DB_PATH, max_rows=50, timeout_seconds=10)
    yield database
    database.close()


def test_executa_consulta_e_retorna_colunas(db):
    result = db.execute("SELECT titulo, ano_lancamento FROM vw_filmes ORDER BY popularidade DESC LIMIT 3")
    assert result.columns == ["titulo", "ano_lancamento"]
    assert len(result.rows) == 3
    assert not result.truncated


def test_trunca_resultado_grande(db):
    result = db.execute("SELECT titulo FROM vw_filmes")
    assert len(result.rows) == db.max_rows
    assert result.truncated


def test_guardrails_sao_aplicados(db):
    with pytest.raises(UnsafeQueryError):
        db.execute("DELETE FROM dim_genres")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM dim_genres",
        "PRAGMA cache_size = 1",
        "ATTACH DATABASE 'x.db' AS x",
        "CREATE TEMP TABLE x (a INT)",
    ],
)
def test_authorizer_bloqueia_escrita_mesmo_sem_guardrails(db, sql):
    # Simula um SQL que escapou do sqlglot: o próprio SQLite deve negar.
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        db._conn.execute(sql)


def test_erro_de_sql_vira_query_error(db):
    with pytest.raises(QueryError, match="no such column"):
        db.execute("SELECT coluna_inexistente FROM vw_filmes")


def test_consulta_lenta_e_interrompida(db, monkeypatch):
    monkeypatch.setattr(db, "timeout_seconds", 0.5)
    with pytest.raises(QueryTimeoutError):
        db.execute("SELECT COUNT(*) FROM dim_people a, dim_people b")


def test_conexao_continua_utilizavel_apos_timeout(db, monkeypatch):
    monkeypatch.setattr(db, "timeout_seconds", 0.5)
    with pytest.raises(QueryTimeoutError):
        db.execute("SELECT COUNT(*) FROM dim_people a, dim_people b")
    monkeypatch.undo()
    assert db.execute("SELECT COUNT(*) FROM vw_filmes").rows == [(95645,)]


def test_dupla_ator_diretor_roda_dentro_do_timeout(db):
    result = db.execute("""
        SELECT a.nome_pessoa, d.nome_pessoa, COUNT(*) AS n
        FROM vw_filme_pessoa a
        JOIN vw_filme_pessoa d ON d.sk_movie_id = a.sk_movie_id AND d.tipo_pessoa = 'Diretor'
        WHERE a.tipo_pessoa = 'Ator' AND a.nome_pessoa <> d.nome_pessoa
        GROUP BY a.sk_person_id, d.sk_person_id
        ORDER BY n DESC LIMIT 1
    """)
    assert result.rows[0][2] > 1


def test_banco_inexistente_da_mensagem_clara(tmp_path):
    with pytest.raises(FileNotFoundError, match="README"):
        Database(tmp_path / "nao_existe.db")
