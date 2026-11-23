"""Exporta las sesiones de la Bolsa de Santiago (XSGO) y NYSE (XNYS) para la app Android.

    cd analisis/backend && python scripts/export_calendars.py

La app móvil no tiene exchange_calendars: usa esta tabla (feriados y cierres anticipados incluidos) hasta 2030.
Formato compacto, por calendario:
  start  = día de la primera sesión (días desde 1970-01-01)
  deltas = días entre sesiones consecutivas
  times  = tramos [cantidad de sesiones, minuto UTC de apertura, minuto UTC de cierre] (cambian con el horario de verano)
"""

from __future__ import annotations

import json
from pathlib import Path

import exchange_calendars as xc
import pandas as pd

OUT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "core" / "calendars.json"
START, END = "2015-01-01", "2030-12-31"


def encode(code: str) -> dict:
    cal = xc.get_calendar(code, start=START, end=END)
    sched = cal.schedule
    days = [(d - pd.Timestamp("1970-01-01")).days for d in sched.index]
    opens = [int((o.tz_convert("UTC") - o.tz_convert("UTC").normalize()).total_seconds() // 60) for o in sched["open"]]
    closes = [int((c.tz_convert("UTC") - c.tz_convert("UTC").normalize()).total_seconds() // 60) for c in sched["close"]]
    for o, c, d in zip(sched["open"], sched["close"], sched.index):
        assert o.tz_convert("UTC").date() == c.tz_convert("UTC").date(), f"{code} {d}: la sesión cruza medianoche UTC"
    times: list[list[int]] = []
    for o, c in zip(opens, closes):
        if times and times[-1][1] == o and times[-1][2] == c:
            times[-1][0] += 1
        else:
            times.append([1, o, c])
    return {"start": days[0], "deltas": [b - a for a, b in zip(days, days[1:])], "times": times}


def main() -> None:
    data = {"generated_from": f"exchange_calendars {xc.__version__}", "range": [START, END],
            "XSGO": encode("XSGO"), "XNYS": encode("XNYS")}
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
