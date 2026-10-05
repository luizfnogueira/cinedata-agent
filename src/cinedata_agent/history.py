"""Histórico de conversas da interface, salvo em SQLite (sobrevive a recarregar a página).

Cada conversa guarda as perguntas e as respostas completas (texto, consultas e
resultado), para que reabrir uma conversa mostre de novo gráficos e tabelas sem
chamar o modelo.
"""

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cinedata_agent.agent import AgentAnswer
from cinedata_agent.cache import answer_from_json, answer_to_json

TITLE_MAX_LENGTH = 48


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    pinned: bool
    updated_at: str


@dataclass(frozen=True)
class StoredTurn:
    question: str
    answer: AgentAnswer | None
    error: str | None


def make_title(question: str) -> str:
    text = " ".join(question.split())
    return text if len(text) <= TITLE_MAX_LENGTH else text[: TITLE_MAX_LENGTH - 1].rstrip() + "…"


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


class ConversationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversas (
                    id TEXT PRIMARY KEY,
                    titulo TEXT NOT NULL,
                    fixada INTEGER NOT NULL DEFAULT 0,
                    criada_em TEXT NOT NULL,
                    atualizada_em TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mensagens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversa_id TEXT NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
                    pergunta TEXT NOT NULL,
                    resposta_json TEXT,
                    erro TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_mensagens_conversa ON mensagens (conversa_id, id);
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Uma conexão por operação (seguro entre as threads do Streamlit),
        # com commit ao final ou rollback em caso de erro.
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create(self, first_question: str) -> str:
        conversation_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversas (id, titulo, criada_em, atualizada_em) VALUES (?, ?, ?, ?)",
                (conversation_id, make_title(first_question), now, now),
            )
        return conversation_id

    def recent(self) -> list[Conversation]:
        """Fixadas primeiro; dentro de cada grupo, as mais recentes primeiro."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, titulo, fixada, atualizada_em FROM conversas ORDER BY fixada DESC, atualizada_em DESC"
            ).fetchall()
        return [Conversation(id=r[0], title=r[1], pinned=bool(r[2]), updated_at=r[3]) for r in rows]

    def turns(self, conversation_id: str) -> list[StoredTurn]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pergunta, resposta_json, erro FROM mensagens WHERE conversa_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [StoredTurn(r[0], answer_from_json(r[1]) if r[1] else None, r[2]) for r in rows]

    def add_turn(self, conversation_id: str, question: str, answer: AgentAnswer | None, error: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mensagens (conversa_id, pergunta, resposta_json, erro) VALUES (?, ?, ?, ?)",
                (conversation_id, question, answer_to_json(answer) if answer else None, error),
            )
            conn.execute("UPDATE conversas SET atualizada_em = ? WHERE id = ?", (_now(), conversation_id))

    def set_pinned(self, conversation_id: str, pinned: bool) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE conversas SET fixada = ? WHERE id = ?", (int(pinned), conversation_id))

    def delete(self, conversation_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM conversas WHERE id = ?", (conversation_id,))

    def exists(self, conversation_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM conversas WHERE id = ?", (conversation_id,)).fetchone() is not None
