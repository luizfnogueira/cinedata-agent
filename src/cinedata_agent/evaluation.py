"""Avaliação do agente: compara o resultado das consultas dele com SQLs de referência.

Métrica: acurácia de execução. O que importa é o resultado da consulta, não o
texto do SQL. Duas consultas diferentes que devolvem os mesmos dados estão certas.

A comparação é tolerante ao que não muda a resposta:
- o agente pode trazer colunas a mais, em outra ordem e com outros nomes (cada
  coluna do gabarito é localizada no resultado do agente pelos valores);
- números são comparados com tolerância de arredondamento;
- em rankings, linhas empatadas podem vir em qualquer ordem, e no último bloco
  de empate (cortado pelo LIMIT) qualquer item com o mesmo valor é aceito.
"""

import math
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cinedata_agent.agent import AgentAnswer
from cinedata_agent.cache import normalize_question
from cinedata_agent.db import Database, QueryResult

REL_TOLERANCE = 0.005  # 0,5%: aceita arredondamento (ex.: nota 6,4567 -> 6,46)
ABS_TOLERANCE = 0.006  # cobre valores pequenos arredondados com 2 casas


@dataclass(frozen=True)
class EvalCase:
    id: str
    categoria: str
    pergunta: str
    tipo: str = "consulta"
    sql: str | None = None
    colunas: list[str] = field(default_factory=list)
    ordenado: bool = False
    ordem_por: str | None = None
    apenas_primeira: bool = False
    contem: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    status: str  # "acerto", "erro" ou "falha" (exceção: o agente não chegou a responder)
    motivo: str = ""
    answer: AgentAnswer | None = None
    elapsed_seconds: float = 0.0
    error: BaseException | None = field(default=None, repr=False)

    @property
    def passed(self) -> bool:
        return self.status == "acerto"


def load_cases(path: Path) -> list[EvalCase]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    cases = [EvalCase(**raw) for raw in data["casos"]]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Há ids de casos repetidos em " + str(path))
    return cases


# ── Comparação de valores e resultados ──────────────────────────────────


def values_match(expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if isinstance(expected, int) and isinstance(actual, int):
            return expected == actual
        return math.isclose(expected, actual, rel_tol=REL_TOLERANCE, abs_tol=ABS_TOLERANCE)
    return normalize_question(str(expected)) == normalize_question(str(actual))


def _sort_key(value: Any) -> tuple:
    if value is None:
        return (0, 0)
    if isinstance(value, (int, float)):
        return (1, value)
    return (2, normalize_question(str(value)))


def _same_multiset(expected: Sequence[Any], actual: Sequence[Any]) -> bool:
    if len(expected) != len(actual):
        return False
    return all(values_match(e, a) for e, a in zip(sorted(expected, key=_sort_key), sorted(actual, key=_sort_key)))


def _rows_match(expected: Sequence[tuple], actual: Sequence[tuple]) -> bool:
    return len(expected) == len(actual) and all(
        len(e) == len(a) and all(values_match(x, y) for x, y in zip(e, a)) for e, a in zip(expected, actual)
    )


def _same_rows_any_order(expected: Sequence[tuple], actual: Sequence[tuple]) -> bool:
    row_key = lambda row: tuple(_sort_key(v) for v in row)  # noqa: E731
    return _rows_match(sorted(expected, key=row_key), sorted(actual, key=row_key))


def map_columns(gold: QueryResult, agent: QueryResult, wanted: Sequence[str]) -> dict[str, int] | str:
    """Para cada coluna do gabarito, acha a coluna do agente com os mesmos valores.

    Devolve {coluna_gabarito: índice_no_agente} ou uma mensagem dizendo qual não foi achada.
    """
    mapping: dict[str, int] = {}
    used: set[int] = set()
    for name in wanted:
        gold_values = [row[gold.columns.index(name)] for row in gold.rows]
        # Prefere a coluna de mesmo nome, se os valores baterem; senão, qualquer uma que bata.
        candidates = sorted(range(len(agent.columns)), key=lambda i: agent.columns[i].lower() != name.lower())
        for i in candidates:
            if i not in used and _same_multiset(gold_values, [row[i] for row in agent.rows]):
                mapping[name] = i
                used.add(i)
                break
        else:
            return f"nenhuma coluna do agente tem os valores de '{name}' do gabarito"
    return mapping


def _tied(a: Any, b: Any) -> bool:
    """Empate de verdade: valores iguais (sem a tolerância de arredondamento de values_match,
    que juntaria valores próximos mas diferentes, como margens de 0,9979 e 0,9967)."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return values_match(a, b)


def _tie_blocks(values: Sequence[Any]) -> list[tuple[int, int]]:
    """Intervalos [início, fim) de posições consecutivas com o mesmo valor."""
    blocks, start = [], 0
    for i in range(1, len(values) + 1):
        if i == len(values) or not _tied(values[start], values[i]):
            blocks.append((start, i))
            start = i
    return blocks


def compare_results(case: EvalCase, gold: QueryResult, agent: QueryResult) -> str | None:
    """None se o resultado do agente responde a pergunta como o gabarito; senão, o motivo."""
    wanted = case.colunas or gold.columns
    gold_rows, agent_rows = gold.rows, agent.rows
    if case.apenas_primeira:
        if not agent_rows:
            return "o agente não retornou linhas"
        gold_rows, agent_rows = gold_rows[:1], agent_rows[:1]
    elif len(agent_rows) != len(gold_rows):
        return f"o agente retornou {len(agent_rows)} linhas; o gabarito tem {len(gold_rows)}"

    blocks: list[tuple[int, int]] = []
    order_values: list[Any] = []
    if case.ordenado and case.ordem_por:
        order_values = [row[gold.columns.index(case.ordem_por)] for row in gold_rows]
        blocks = _tie_blocks(order_values)

    # O último bloco de empate pode ter itens diferentes (o LIMIT corta o empate),
    # então não entra na localização das colunas.
    strict_end = blocks[-1][0] if len(blocks) > 1 else len(gold_rows)
    gold_view = QueryResult(gold.columns, gold_rows[:strict_end], gold.truncated, 0)
    agent_view = QueryResult(agent.columns, agent_rows[:strict_end], agent.truncated, 0)
    mapping = map_columns(gold_view, agent_view, wanted)
    if isinstance(mapping, str):
        return mapping

    gold_proj = [tuple(row[gold.columns.index(c)] for c in wanted) for row in gold_rows]
    agent_proj = [tuple(row[mapping[c]] for c in wanted) for row in agent_rows]

    if not case.ordenado:
        return None if _same_rows_any_order(gold_proj, agent_proj) else "as linhas não correspondem às do gabarito"

    if not case.ordem_por:
        return None if _rows_match(gold_proj, agent_proj) else "a ordem das linhas difere do gabarito"

    for n, (start, end) in enumerate(blocks):
        is_last = n == len(blocks) - 1
        if is_last and len(blocks) > 1:
            # Empate cortado pelo LIMIT: qualquer item com o mesmo valor serve.
            # Se a coluna de ordem foi comparada, ela já garante o valor; senão aceita.
            if case.ordem_por in wanted:
                idx = wanted.index(case.ordem_por)
                if not all(values_match(order_values[start], row[idx]) for row in agent_proj[start:end]):
                    return f"posições {start + 1}–{end} não têm o valor esperado"
            continue
        if not _same_rows_any_order(gold_proj[start:end], agent_proj[start:end]):
            return f"posições {start + 1}–{end} diferem do gabarito"
    return None


def check_answer_text(case: EvalCase, answer: AgentAnswer) -> str | None:
    text = normalize_question(answer.answer)
    missing = [t for t in case.contem if normalize_question(t) not in text]
    return f"a resposta não menciona: {', '.join(missing)}" if missing else None


def judge(case: EvalCase, answer: AgentAnswer, db: Database) -> tuple[str, str]:
    """Classifica a resposta do agente: ("acerto" | "erro", motivo)."""
    if case.tipo == "recusa":
        if answer.final_query is not None:
            return "erro", "o agente executou SQL em vez de recusar"
        return "acerto", ""

    final = answer.final_query
    if final is None:
        return "erro", "o agente não executou nenhuma consulta com sucesso"
    gold = db.execute(case.sql)
    reason = compare_results(case, gold, final.result) or check_answer_text(case, answer)
    return ("erro", reason) if reason else ("acerto", "")


def run_case(case: EvalCase, ask: Callable[[str], AgentAnswer], db: Database, describe_error: Callable[[BaseException], str]) -> CaseResult:
    start = time.monotonic()
    try:
        answer = ask(case.pergunta)
    except Exception as e:  # noqa: BLE001 - qualquer falha do agente vira "falha" no relatório
        return CaseResult(case, "falha", describe_error(e), None, time.monotonic() - start, error=e)
    status, reason = judge(case, answer, db)
    return CaseResult(case, status, reason, answer, time.monotonic() - start)


# ── Relatório ───────────────────────────────────────────────────────────

STATUS_ICON = {"acerto": "✅", "erro": "❌", "falha": "⚠️"}


def summarize(results: Sequence[CaseResult]) -> dict[str, Any]:
    by_category: dict[str, list[int]] = {}
    for r in results:
        hits_total = by_category.setdefault(r.case.categoria, [0, 0])
        hits_total[0] += r.passed
        hits_total[1] += 1
    answered = [r for r in results if r.answer is not None]
    return {
        "acertos": sum(r.passed for r in results),
        "total": len(results),
        "por_categoria": by_category,
        "requisicoes": sum(r.answer.requests for r in answered),
        "do_cache": sum(r.answer.cached for r in answered),
        "tempo_medio_s": sum(r.elapsed_seconds for r in answered if not r.answer.cached)
        / max(1, sum(not r.answer.cached for r in answered)),
    }


def _pct(hits: int, total: int) -> str:
    return f"{100 * hits / total:.0f}%" if total else "-"


def markdown_report(results: Sequence[CaseResult], generated_at: str) -> str:
    summary = summarize(results)
    lines = [
        "# Avaliação do agente CineData",
        "",
        f"Gerado em {generated_at}. Acurácia de execução: o resultado da consulta do agente é comparado",
        "com o de um SQL de referência (ver `evals/casos.toml`).",
        "",
        f"**Acertos: {summary['acertos']}/{summary['total']} ({_pct(summary['acertos'], summary['total'])})**"
        f" · requisições gastas: {summary['requisicoes']} · respostas do cache: {summary['do_cache']}"
        f" · tempo médio por pergunta: {summary['tempo_medio_s']:.1f} s",
        "",
        "| Categoria | Acertos |",
        "|---|---|",
    ]
    for category, (hits, total) in summary["por_categoria"].items():
        lines.append(f"| {category} | {hits}/{total} ({_pct(hits, total)}) |")
    lines += ["", "| | Caso | Pergunta | Modelo | Req. | Tempo | Observação |", "|---|---|---|---|---|---|---|"]
    for r in results:
        a = r.answer
        model = (a.model_name or "-") if a else "-"
        requests = ("cache" if a.cached else str(a.requests)) if a else "-"
        lines.append(
            f"| {STATUS_ICON[r.status]} | {r.case.id} | {r.case.pergunta} | {model} | {requests}"
            f" | {r.elapsed_seconds:.1f} s | {r.motivo} |"
        )
    failures = [r for r in results if not r.passed]
    if failures:
        lines += ["", "## Casos com erro", ""]
        for r in failures:
            lines += [f"### {r.case.id}: {r.case.pergunta}", "", f"Motivo: {r.motivo}", ""]
            if r.answer and r.answer.queries:
                lines += ["SQL do agente:", "```sql", r.answer.queries[-1].sql.strip(), "```", ""]
            if r.case.sql:
                lines += ["SQL de referência:", "```sql", r.case.sql.strip(), "```", ""]
    return "\n".join(lines) + "\n"
