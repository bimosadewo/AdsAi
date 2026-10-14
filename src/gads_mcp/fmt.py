"""Format angka & uang gaya Indonesia (Rp1.234.567 / 1.234,5)."""

from __future__ import annotations


def rupiah(value: float | None) -> str:
    if value is None:
        return "-"
    return "Rp" + f"{value:,.0f}".replace(",", ".")


def number(value: float | None, decimals: int = 0) -> str:
    if value is None:
        return "-"
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")
