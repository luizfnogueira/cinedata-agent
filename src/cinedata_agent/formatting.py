"""Formatação de valores para leitura humana (e para o modelo).

Valores de dinheiro e margem saem do banco crus (ex.: -1037292.92, 0.7604).
Converter a escala ficava a cargo do modelo, e no smoke test o Nemotron
apresentou R$ 1,0 mi como "R$ 1,0 mil". Formatar aqui elimina essa conta.

A moeda e a margem são reconhecidas pelo nome da coluna: o system prompt pede
ao modelo que mantenha os sufixos _brl/_usd e a palavra "margem" nos aliases.
"""

_SCALES = ((1e9, "bi"), (1e6, "mi"), (1e3, "mil"))


def _pt_number(value: float, decimals: int) -> str:
    """Número com vírgula decimal e ponto de milhar: 1234.5 -> '1.234,50'."""
    return f"{value:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def format_money(value: float, symbol: str = "R$") -> str:
    sign = "-" if value < 0 else ""
    amount = abs(value)
    for threshold, suffix in _SCALES:
        if amount >= threshold:
            return f"{sign}{symbol} {_pt_number(amount / threshold, 2)} {suffix}"
    return f"{sign}{symbol} {_pt_number(amount, 2)}"


def format_percent(fraction: float) -> str:
    return f"{_pt_number(fraction * 100, 1)}%"


def money_symbol(column: str) -> str | None:
    name = column.lower()
    if "brl" in name:
        return "R$"
    if "usd" in name:
        return "US$"
    return None


def is_margin(column: str) -> bool:
    """Coluna de margem em fração (0.25 = 25%). Aliases já em porcentagem (pct, percent) ficam de fora."""
    name = column.lower()
    return "margem" in name and not any(tag in name for tag in ("pct", "percent", "perc"))


def format_cell(column: str, value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if symbol := money_symbol(column):
            return format_money(value, symbol)
        if is_margin(column):
            return format_percent(value)
        if isinstance(value, float):
            return f"{value:.2f}" if abs(value) >= 1 else f"{value:.4f}"
    return str(value)
