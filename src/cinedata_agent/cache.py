"""Cache de respostas do agente em SQLite: pergunta repetida custa 0 requisições.

A chave combina a pergunta normalizada com uma impressão digital do que
influencia a resposta (system prompt, arquivo do banco e modelo). Se qualquer
um mudar, por exemplo ao ajustar o prompt, as respostas antigas deixam de ser
encontradas e nada desatualizado é servido.
"""

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from cinedata_agent.agent import AgentAnswer, ExecutedQuery
from cinedata_agent.db import QueryResult


def normalize_question(question: str) -> str:
    """Ignora maiúsculas, acentos, pontuação e espaços extras: 'Top 10 filmes?' == 'top 10 filmes'."""
    text = unicodedata.normalize("NFKD", question.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s$%]", " ", text)
    return " ".join(text.split())


def cache_fingerprint(system_prompt: str, db_path: Path, model_name: str) -> str:
    stat = db_path.stat()
    raw = f"{system_prompt}\n{db_path.name}:{stat.st_size}:{stat.st_mtime_ns}\n{model_name}"
    return hashlib.sha256(raw.encode()).hexdigest()


def answer_to_json(answer: AgentAnswer) -> str:
    queries = [
        {
            "sql": q.sql,
            "error": q.error,
            "result": None
            if q.result is None
            else {
                "columns": q.result.columns,
                "rows": [list(row) for row in q.result.rows],
                "truncated": q.result.truncated,
                "elapsed_seconds": q.result.elapsed_seconds,
            },
        }
        for q in answer.queries
    ]
    payload = {"question": answer.question, "answer": answer.answer, "model_name": answer.model_name, "queries": queries}
    return json.dumps(payload, ensure_ascii=False, default=str)


def answer_from_json(raw: str) -> AgentAnswer:
    payload = json.loads(raw)
    queries = [
        ExecutedQuery(
            sql=q["sql"],
            error=q["error"],
            result=None
            if q["result"] is None
            else QueryResult(
                columns=q["result"]["columns"],
                rows=[tuple(row) for row in q["result"]["rows"]],
                truncated=q["result"]["truncated"],
                elapsed_seconds=q["result"]["elapsed_seconds"],
            ),
        )
        for q in payload["queries"]
    ]
    return AgentAnswer(
        question=payload["question"],
        answer=payload["answer"],
        queries=queries,
        model_name=payload["model_name"],
        requests=0,
        messages=[],
        cached=True,
    )


class AnswerCache:
    def __init__(self, path: Path, fingerprint: str) -> None:
        self.path = path
        self.fingerprint = fingerprint
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS respostas (
                    chave TEXT PRIMARY KEY,
                    pergunta TEXT NOT NULL,
                    resposta_json TEXT NOT NULL,
                    criado_em TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Uma conexão por operação (seguro entre as threads do Streamlit),
        # com commit ao final ou rollback em caso de erro.
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def key(self, question: str) -> str:
        return hashlib.sha256(f"{self.fingerprint}\n{normalize_question(question)}".encode()).hexdigest()

    def get(self, question: str) -> AgentAnswer | None:
        with self._connect() as conn:
            row = conn.execute("SELECT resposta_json FROM respostas WHERE chave = ?", (self.key(question),)).fetchone()
        return answer_from_json(row[0]) if row else None

    def put(self, answer: AgentAnswer) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO respostas (chave, pergunta, resposta_json, criado_em) VALUES (?, ?, ?, ?)",
                (self.key(answer.question), answer.question, answer_to_json(answer), datetime.now().isoformat(timespec="seconds")),
            )

    def clear(self) -> int:
        """Apaga todas as respostas guardadas e devolve quantas eram."""
        with self._connect() as conn:
            return conn.execute("DELETE FROM respostas").rowcount
