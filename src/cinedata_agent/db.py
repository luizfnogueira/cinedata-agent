"""Acesso somente leitura ao banco da camada Gold.

Toda consulta do agente passa por `Database.execute`, que aplica três camadas:
1. guardrails.validate_sql — análise do SQL com sqlglot;
2. authorizer do SQLite — o próprio motor nega qualquer ação que não seja leitura;
3. conexão em modo read-only (`mode=ro`) — o arquivo não pode ser alterado.

Além disso, cada consulta tem um tempo máximo e o resultado é truncado em
`max_rows` linhas, para não estourar o contexto do modelo.
"""

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cinedata_agent.config import Settings
from cinedata_agent.guardrails import BLOCKED_FUNCTIONS, validate_sql
from cinedata_agent.semantic import create_semantic_views, reference_year

# Ações que o authorizer libera durante as consultas do agente. Todo o resto
# (INSERT, UPDATE, DELETE, CREATE, DROP, PRAGMA, ATTACH...) é negado.
_ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
})

# Número de instruções da VM do SQLite entre cada checagem de timeout
_PROGRESS_CHECK_INTERVAL = 10_000


class QueryError(Exception):
    """Erro ao executar a consulta. A mensagem é devolvida ao modelo para correção."""


class QueryTimeoutError(QueryError):
    pass


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool
    elapsed_seconds: float


def _read_only_authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger: str | None) -> int:
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and arg2 and arg2.lower() in BLOCKED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class Database:
    """Conexão única, thread-safe, somente leitura, com as views semânticas criadas."""

    def __init__(self, path: Path, max_rows: int = 50, timeout_seconds: float = 10.0) -> None:
        if not path.exists():
            raise FileNotFoundError(
                f"Banco não encontrado em {path}. Baixe o cinerocket.db (link no README) "
                "e coloque-o na raiz do projeto, ou defina CINEDATA_DB_PATH no .env."
            )
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._deadline: float | None = None

        # check_same_thread=False: o Streamlit atende cada sessão numa thread; o lock serializa o acesso.
        self._conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        self._conn.execute("PRAGMA cache_size = -262144")  # 256 MB de cache de páginas
        self._conn.execute("PRAGMA mmap_size = 1073741824")
        self._conn.execute("PRAGMA temp_store = MEMORY")
        create_semantic_views(self._conn)
        self.reference_year = reference_year(self._conn)

        # A partir daqui, só leitura: views e tabelas TEMP já foram criadas.
        self._conn.set_authorizer(_read_only_authorizer)
        self._conn.set_progress_handler(self._check_timeout, _PROGRESS_CHECK_INTERVAL)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        return cls(settings.db_path, settings.max_rows, settings.query_timeout_seconds)

    def _check_timeout(self) -> int:
        # Retornar valor diferente de zero interrompe a consulta em andamento.
        return int(self._deadline is not None and time.monotonic() > self._deadline)

    def execute(self, sql: str) -> QueryResult:
        """Valida e executa uma consulta de leitura.

        Levanta guardrails.UnsafeQueryError se o SQL for recusado, ou QueryError
        se o SQLite falhar (coluna inexistente, timeout etc.).
        """
        sql = validate_sql(sql)
        with self._lock:
            start = time.monotonic()
            self._deadline = start + self.timeout_seconds
            try:
                cursor = self._conn.execute(sql)
                rows = cursor.fetchmany(self.max_rows + 1)
                columns = [col[0] for col in cursor.description or []]
                cursor.close()
            except sqlite3.OperationalError as e:
                if str(e) == "interrupted":
                    raise QueryTimeoutError(
                        f"A consulta passou de {self.timeout_seconds:.0f} s e foi interrompida. "
                        "Simplifique-a: filtre antes de juntar tabelas ou reduza os joins."
                    ) from e
                raise QueryError(f"Erro do SQLite: {e}") from e
            except sqlite3.DatabaseError as e:
                raise QueryError(f"Erro do SQLite: {e}") from e
            finally:
                self._deadline = None
            elapsed = time.monotonic() - start

        return QueryResult(
            columns=columns,
            rows=rows[: self.max_rows],
            truncated=len(rows) > self.max_rows,
            elapsed_seconds=elapsed,
        )

    def close(self) -> None:
        self._conn.close()
