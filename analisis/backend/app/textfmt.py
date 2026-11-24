"""Formato de números en español (punto de miles, coma decimal)."""

from __future__ import annotations

import math


def num(v, nd: int = 2, suffix: str = "") -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    s = f"{v:,.{nd}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return s + suffix


def pct(v, nd: int = 1, signed: bool = True) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return ("+" if signed and v > 0 else "") + num(v, nd, " %")


def days(n) -> str:
    n = int(n)
    return "hoy" if n == 0 else ("hace 1 día" if n == 1 else f"hace {n} días")
