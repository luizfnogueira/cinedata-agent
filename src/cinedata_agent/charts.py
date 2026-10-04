"""Gráfico automático para o resultado de uma consulta, quando ele ajuda a ler a resposta.

Regras (a forma segue o que o dado precisa mostrar):
- comparação entre itens (filme, gênero, pessoa x valor): barras horizontais, na
  ordem do resultado (o agente já ordena os rankings);
- evolução por ano: linha com marcadores;
- sem gráfico quando há só 1-2 linhas (o texto basta), mais de 25 (a tabela é
  melhor) ou mais de uma métrica concorrendo (ambíguo: fica só a tabela).
Uma só série, uma só cor; o valor formatado aparece no tooltip.
"""

from dataclasses import dataclass
from typing import Any

import altair as alt
import pandas as pd

from cinedata_agent.db import QueryResult
from cinedata_agent.formatting import format_cell, format_number, is_margin, money_symbol

MIN_ROWS, MAX_ROWS = 3, 25

# Uma série = um matiz (azul da paleta validada), com um passo para cada tema.
SERIES_COLOR = {"light": "#2a78d6", "dark": "#3987e5"}

_SUPPORT_PREFIXES = ("qtd", "quantidade", "count", "n_", "votos")


@dataclass(frozen=True)
class ChartPlan:
    kind: str  # "bar" ou "line"
    labels: list[str]  # rótulo de cada linha (barras) ou ano (linha)
    values: list[float]  # já na escala do eixo
    tooltips: list[str]  # valor formatado para leitura
    axis_title: str


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _column_kind(values: list[Any]) -> str | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    if all(_is_number(v) for v in present):
        return "number"
    if all(isinstance(v, str) for v in present):
        return "text"
    return None


def _is_year(name: str) -> bool:
    return name.lower().startswith("ano")


def _humanize(name: str) -> str:
    words = [w for w in name.lower().split("_") if w not in ("brl", "usd")]
    return " ".join(words).capitalize()


def _scale(column: str, values: list[float]) -> tuple[list[float], str]:
    """Põe dinheiro em mil/mi/bi e margem em %, para o eixo ficar legível."""
    title = _humanize(column)
    if symbol := money_symbol(column):
        largest = max((abs(v) for v in values), default=0)
        for factor, unit in ((1e9, "bi"), (1e6, "mi"), (1e3, "mil")):
            if largest >= factor:
                return [v / factor for v in values], f"{title} ({symbol} {unit})"
        return values, f"{title} ({symbol})"
    if is_margin(column):
        return [v * 100 for v in values], f"{title} (%)"
    return values, title


def _unique(labels: list[str]) -> list[str]:
    """Títulos repetidos existem no catálogo; o eixo precisa de rótulos únicos."""
    seen: dict[str, int] = {}
    result = []
    for label in labels:
        seen[label] = seen.get(label, 0) + 1
        result.append(label if seen[label] == 1 else f"{label} ({seen[label]})")
    return result


def plan_chart(result: QueryResult) -> ChartPlan | None:
    rows = result.rows
    if not (MIN_ROWS <= len(rows) <= MAX_ROWS):
        return None
    columns = {name: [row[i] for row in rows] for i, name in enumerate(result.columns)}
    kinds = {name: _column_kind(values) for name, values in columns.items()}

    text_cols = [c for c, k in kinds.items() if k == "text"]
    year_cols = [c for c, k in kinds.items() if k == "number" and _is_year(c)]
    numeric = [c for c, k in kinds.items() if k == "number" and not _is_year(c) and not c.lower().startswith("sk_")]
    metrics = [c for c in numeric if not c.lower().startswith(_SUPPORT_PREFIXES)]
    if not metrics and len(numeric) == 1:
        metrics = numeric  # ex.: gênero x qtd_filmes: a contagem é a métrica
    if len(metrics) != 1:
        return None
    metric = metrics[0]
    raw = columns[metric]
    if any(v is None for v in raw):
        return None
    values, axis_title = _scale(metric, [float(v) for v in raw])
    if money_symbol(metric) or is_margin(metric):
        tooltips = [format_cell(metric, v) for v in raw]
    else:
        tooltips = [format_number(v) for v in raw]

    if text_cols:
        labels = [" · ".join(str(columns[c][i]) for c in text_cols[:2]) for i in range(len(rows))]
        if year_cols:
            labels = [f"{label} ({columns[year_cols[0]][i]})" for i, label in enumerate(labels)]
        return ChartPlan("bar", _unique(labels), values, tooltips, axis_title)
    if year_cols:
        return ChartPlan("line", [str(v) for v in columns[year_cols[0]]], values, tooltips, axis_title)
    return None


def build_chart(plan: ChartPlan, theme: str = "light") -> alt.Chart:
    color = SERIES_COLOR.get(theme, SERIES_COLOR["light"])
    data = pd.DataFrame({"rotulo": plan.labels, "valor": plan.values, "formatado": plan.tooltips})
    tooltip = [alt.Tooltip("rotulo:N", title=""), alt.Tooltip("formatado:N", title=plan.axis_title)]

    if plan.kind == "bar":
        return (
            alt.Chart(data)
            .mark_bar(color=color, cornerRadiusEnd=4, height={"band": 0.7})
            .encode(
                y=alt.Y("rotulo:N", sort=plan.labels, title=None, axis=alt.Axis(labelLimit=260)),
                x=alt.X("valor:Q", title=plan.axis_title),
                tooltip=tooltip,
            )
            .properties(height=28 * len(plan.labels) + 40)
        )

    return (
        alt.Chart(data)
        .mark_line(color=color, strokeWidth=2, point=alt.OverlayMarkDef(color=color, size=64, filled=True))
        .encode(
            x=alt.X("rotulo:O", title="Ano", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("valor:Q", title=plan.axis_title, scale=alt.Scale(zero=False)),
            tooltip=tooltip,
        )
        .properties(height=280)
    )
