"""Validação do SQL gerado pelo modelo antes de chegar ao banco.

Primeira das três camadas de proteção (as outras duas ficam em db.py: o
authorizer do SQLite e a conexão em modo read-only). Aqui o SQL é analisado
com sqlglot, não com regex, para que comentários, strings e CTEs não enganem
a validação.

As mensagens de erro são escritas para o modelo: voltam pela tool `run_sql`
e devem dizer como corrigir a consulta.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from cinedata_agent.semantic import VIEWS

GOLD_TABLES = frozenset({
    "dim_movies",
    "fact_movies_performance",
    "dim_genres",
    "dim_people",
    "dim_companies",
    "dim_reviews",
    "movie_reviews",
    "bridge_movie_genre",
    "bridge_movie_person",
    "bridge_movie_company",
})

ALLOWED_TABLES = GOLD_TABLES | frozenset(VIEWS)

BLOCKED_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})

MAX_SQL_LENGTH = 5_000


class UnsafeQueryError(ValueError):
    """O SQL foi recusado pelos guardrails. A mensagem explica o motivo ao modelo."""


def validate_sql(sql: str) -> str:
    """Valida que `sql` é uma única consulta de leitura sobre tabelas permitidas.

    Retorna o SQL sem espaços e ponto e vírgula finais. Levanta UnsafeQueryError
    caso contrário.
    """
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise UnsafeQueryError("A consulta está vazia.")
    if len(sql) > MAX_SQL_LENGTH:
        raise UnsafeQueryError(f"A consulta passa de {MAX_SQL_LENGTH} caracteres. Simplifique-a.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except ParseError as e:
        raise UnsafeQueryError(f"SQL inválido para SQLite: {e}") from e

    if len(statements) != 1:
        raise UnsafeQueryError("Envie exatamente uma consulta por vez, sem vários comandos separados por ';'.")

    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise UnsafeQueryError(
            f"Apenas consultas de leitura (SELECT/WITH) são permitidas; recebido: {statement.key.upper()}."
        )

    # Comandos de escrita escondidos dentro de uma consulta (ex.: CTE com DELETE)
    for node in statement.walk():
        if isinstance(node, (exp.DML, exp.DDL, exp.Command, exp.Pragma)):
            raise UnsafeQueryError("A consulta contém um comando que não é de leitura.")

    for func in statement.find_all(exp.Anonymous, exp.Func):
        name = (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()
        if name in BLOCKED_FUNCTIONS:
            raise UnsafeQueryError(f"A função {name} não é permitida.")

    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    for table in statement.find_all(exp.Table):
        name = table.name.lower()
        if not name or name in cte_names:
            continue
        if name not in ALLOWED_TABLES:
            allowed = ", ".join(sorted(VIEWS))
            raise UnsafeQueryError(
                f"A tabela '{table.name}' não existe ou não é permitida. Use as views: {allowed}."
            )

    return sql
