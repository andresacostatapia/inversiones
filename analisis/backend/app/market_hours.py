"""Horario de mercado (con feriados) y etiqueta de frescura de cada dato."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

CALENDARS = {"CL": "XSGO", "US": "XNYS"}  # Bolsa de Santiago, NYSE (los ETF de NYSE Arca siguen el mismo horario)
MARKET_NAMES = {"CL": "Bolsa de Santiago", "US": "EE.UU.", "FX": "Dólar (USD/CLP)", "GLB": "Futuros y divisas",
                "CRY": "Cripto", "OTHER": "Otras bolsas"}
CHICAGO = ZoneInfo("America/Chicago")


def _globex_open(now: datetime) -> bool:
    """Futuros y divisas (CME Globex): de domingo 17:00 a viernes 16:00 hora de Chicago, con pausa diaria
    de 16:00 a 17:00. Sin feriados (aproximación: en un feriado se ve "con retraso" en vez de "cerrado")."""
    t = now.astimezone(CHICAGO)
    wd, h = t.weekday(), t.hour
    if wd == 5 or (wd == 6 and h < 17) or (wd == 4 and h >= 16):
        return False
    return h != 16


@lru_cache
def _calendar(code: str):
    import exchange_calendars as xc

    return xc.get_calendar(code)


def is_open(market: str, now: datetime | None = None) -> bool:
    """El dólar (USD/CLP) se trata con el horario de Santiago: fuera de él casi no hay operaciones
    y el precio de Yahoo se queda quieto, así que se muestra como "cierre" y no como "desactualizado"."""
    now = now or datetime.now(timezone.utc)
    if market == "GLB":
        return _globex_open(now)
    if market == "CRY":
        return True
    if market not in ("CL", "US", "FX"):
        return False  # otras bolsas: horario desconocido (se consulta cada hora y se muestra la hora del dato)
    cal = _calendar(CALENDARS["CL" if market == "FX" else market])
    ts = pd.Timestamp(now).tz_convert("UTC").floor("min")
    if ts < cal.first_minute or ts > cal.last_minute:
        return False
    return bool(cal.is_open_on_minute(ts))


def session_info(market: str, now: datetime | None = None) -> dict:
    """Si está abierto y cuándo abre/cierra la sesión actual o la próxima (UTC, ISO)."""
    now = now or datetime.now(timezone.utc)
    open_now = is_open(market, now)
    info = {"market": market, "name": MARKET_NAMES.get(market, market), "open": open_now}
    if market not in ("CL", "US", "FX"):
        return info
    cal = _calendar(CALENDARS["CL" if market == "FX" else market])
    ts = pd.Timestamp(now).tz_convert("UTC").floor("min")
    try:
        if open_now:
            session = cal.minute_to_session(ts)
            info["closes_at"] = cal.session_close(session).isoformat()
        else:
            info["next_open"] = cal.next_open(ts).isoformat()
    except (ValueError, KeyError):  # fuera del rango del calendario
        pass
    return info


def freshness(
    as_of: datetime | None,
    *,
    realtime: bool,
    market_open: bool,
    now: datetime | None = None,
    stale_after_minutes: int = 30,
    tz: str = "America/Santiago",
    daily: bool = False,
    hours_known: bool = True,
) -> dict:
    """Etiqueta para la UI: si el dato es en tiempo real, con retraso, de cierre o desactualizado, y su hora."""
    now = now or datetime.now(timezone.utc)
    if as_of is None:
        return {"kind": "sin_datos", "label": "Sin datos", "as_of_local": None, "age_minutes": None}
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    local = as_of.astimezone(ZoneInfo(tz))
    age = max(0.0, (now - as_of).total_seconds() / 60)
    hhmm = local.strftime("%H:%M")
    same_day = local.date() == now.astimezone(ZoneInfo(tz)).date()
    when = hhmm if same_day else local.strftime("%d/%m %H:%M")

    if daily:  # p. ej. el dólar observado: un valor oficial por día hábil
        kind, label = "diario", f"Dato diario oficial del {local.strftime('%d/%m')}"
    elif not hours_known:  # otras bolsas: no se sabe si está abierta; solo la hora del dato
        kind, label = "cierre", f"Último precio {when} · horario de esa bolsa no disponible"
    elif not market_open:
        kind, label = "cierre", f"Mercado cerrado · último precio {when}"
    elif age > stale_after_minutes:
        kind, label = "desactualizado", f"Desactualizado · dato de {when} (hace {_age_text(age)})"
    elif realtime and age <= 2:
        kind, label = "tiempo_real", f"Tiempo real · {when}"
    else:
        kind, label = "retraso", f"Con retraso · dato de {when} (hace {_age_text(age)})"
    return {"kind": kind, "label": label, "as_of_local": local.isoformat(), "age_minutes": round(age, 1)}


def _age_text(minutes: float) -> str:
    if minutes < 1:
        return "menos de 1 min"
    if minutes < 90:
        return f"{int(minutes)} min"
    td = timedelta(minutes=minutes)
    h = int(td.total_seconds() // 3600)
    return f"{h} h" if h < 48 else f"{h // 24} días"
