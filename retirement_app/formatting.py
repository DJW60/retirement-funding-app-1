from __future__ import annotations


def format_currency(value: float | None) -> str:
    if value is None:
        return "-"
    return f"${float(value):,.0f}"


def format_age(value: int | None) -> str:
    if value is None:
        return "-"
    return f"Age {int(value)}"


def format_percentage(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.{int(decimals)}f}%"
