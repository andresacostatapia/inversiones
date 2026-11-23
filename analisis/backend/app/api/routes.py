"""Rutas REST de la fase 1: watchlist, precios, historia con indicadores, dólar y estado."""

from __future__ import annotations

import asyncio
import math
from typing import Literal
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import indicators as ind
from ..market_data import InvalidSymbol, MarketData, normalize_symbol
from ..market_hours import session_info
from ..providers.base import SymbolNotFound
from ..providers.fx import PAIR
from ..signal_service import NoHistory, SignalService
from ..strategies import STRATEGIES

DISCLAIMER = "Herramienta educativa, no es asesoría financiera."
PERIODS = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "2y": 731, "5y": 1827, "10y": 3653, "max": None}
Period = Literal["1m", "3m", "6m", "1y", "2y", "5y", "10y", "max"]

router = APIRouter(prefix="/api")


def md(request: Request) -> MarketData:
    return request.app.state.md


def _json_safe(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


class SymbolIn(BaseModel):
    symbol: str


@router.get("/status")
def status(request: Request):
    m = md(request)
    poller = request.app.state.poller
    return {
        "disclaimer": DISCLAIMER,
        "now": datetime.now(timezone.utc).isoformat(),
        "timezone": m.cfg.general.timezone,
        "markets": [session_info(k) for k in ("CL", "US", "FX")],
        "providers": m.router.status(),
        "fx_sources_errors": m.fx.last_errors,
        "poll_seconds": m.cfg.data.poll_seconds,
        "last_cycle": poller.last_cycle.isoformat() if poller and poller.last_cycle else None,
        "last_cycle_error": poller.last_cycle_error if poller else None,
    }


@router.get("/watchlist")
def get_watchlist(request: Request):
    return md(request).quotes_view()


@router.post("/watchlist", status_code=201)
async def add_to_watchlist(body: SymbolIn, request: Request):
    try:
        return await asyncio.to_thread(md(request).add_symbol, body.symbol)
    except InvalidSymbol as e:
        raise HTTPException(422, str(e))
    except SymbolNotFound as e:
        raise HTTPException(404, f"No hay datos para {body.symbol.upper()}. Revisa el símbolo (Chile usa el sufijo .SN, p. ej. SQM-B.SN). Detalle: {e}")
    except Exception as e:
        raise HTTPException(502, f"No se pudo validar {body.symbol.upper()}: los proveedores no respondieron ({e})")


@router.delete("/watchlist/{symbol}")
def remove_from_watchlist(symbol: str, request: Request):
    try:
        removed = md(request).remove_symbol(symbol)
    except InvalidSymbol as e:
        raise HTTPException(422, str(e))
    if not removed:
        raise HTTPException(404, f"{symbol.upper()} no está en la watchlist")
    return {"removed": symbol.upper()}


@router.get("/quotes/{symbol}")
def get_quote(symbol: str, request: Request):
    try:
        return md(request).quote_view(normalize_symbol(symbol))
    except InvalidSymbol as e:
        raise HTTPException(422, str(e))


def _bars_with_indicators(m: MarketData, symbol: str):
    bars = m.bars(symbol)
    if bars.empty:
        raise HTTPException(404, f"Sin historia guardada para {symbol}. ¿Está en la watchlist? Prueba POST /api/refresh.")
    return ind.compute_all(bars)


@router.get("/instruments/{symbol}/history")
def history(symbol: str, request: Request, period: Period = "1y"):
    """Velas diarias + indicadores. Se calculan sobre toda la historia y luego se recorta el periodo,
    para que la SMA200 sea válida desde el primer día mostrado."""
    try:
        symbol = normalize_symbol(symbol)
    except InvalidSymbol as e:
        raise HTTPException(422, str(e))
    df = _bars_with_indicators(md(request), symbol)
    days = PERIODS[period]
    if days:
        df = df[df.index >= df.index[-1] - timedelta(days=days)]
    df = df.reset_index()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    rows = [{k: _json_safe(v) for k, v in r.items()} for r in df.astype(object).where(df.notna(), None).to_dict("records")]
    return {"symbol": symbol, "period": period, "bars": rows}


@router.get("/instruments/{symbol}/indicators")
def indicators(symbol: str, request: Request):
    """Último valor de cada indicador, métricas básicas y precio actual."""
    try:
        symbol = normalize_symbol(symbol)
    except InvalidSymbol as e:
        raise HTTPException(422, str(e))
    m = md(request)
    df = _bars_with_indicators(m, symbol)
    snap = ind.latest_snapshot(df)
    quote = m.quote_view(symbol)
    return {
        "symbol": symbol,
        "quote": quote,
        "bar_date": df.index[-1].date().isoformat(),
        # Con el mercado abierto, la última vela es la de hoy y aún puede cambiar.
        "provisional": quote["freshness"]["kind"] not in ("cierre", "sin_datos"),
        "indicators": snap,
        "metrics": {k: _json_safe(v) for k, v in ind.basic_metrics(df).items()},
        "history_days": len(df),
    }


@router.get("/fx")
def fx(request: Request):
    return md(request).quote_view(PAIR)


@router.post("/refresh")
async def refresh(request: Request):
    """Fuerza una actualización inmediata de todos los precios (respetando los límites de cada API)."""
    m = md(request)
    m.last_refresh.clear()
    return await asyncio.to_thread(request.app.state.poller.cycle)


# --- Fase 2: señales y backtest ------------------------------------------------

def sig(request: Request) -> SignalService:
    return request.app.state.signals


def _symbol(raw: str) -> str:
    try:
        return normalize_symbol(raw)
    except InvalidSymbol as e:
        raise HTTPException(422, str(e))


@router.get("/strategies")
def strategies(request: Request):
    cfg = md(request).cfg.signals
    return {
        "thresholds": cfg.thresholds.model_dump(),
        "volume_effect": cfg.volume_effect,
        "strategies": [{"key": k, "label": s.label, "description": s.description, "weight": cfg.weights.get(k, 0)}
                       for k, s in STRATEGIES.items()],
    }


@router.get("/signals")
def signals_all(request: Request):
    """Señal actual de cada símbolo de la watchlist."""
    return {"disclaimer": DISCLAIMER, "signals": sig(request).all_current()}


@router.get("/signals/{symbol}")
def signal_one(symbol: str, request: Request):
    symbol = _symbol(symbol)
    try:
        return {"disclaimer": DISCLAIMER, **sig(request).current(symbol)}
    except NoHistory as e:
        raise HTTPException(404, f"{e}. ¿Está en la watchlist? Prueba POST /api/refresh.")


@router.get("/signals/{symbol}/history")
def signal_history(symbol: str, request: Request, period: Period = "1y"):
    """Puntaje diario de cada estrategia y del combinado, con su etiqueta."""
    symbol = _symbol(symbol)
    try:
        return {"symbol": symbol, "period": period, "points": sig(request).history(symbol, PERIODS[period])}
    except NoHistory as e:
        raise HTTPException(404, str(e))


class BacktestIn(BaseModel):
    symbol: str
    years: float | None = Field(None, gt=0, le=50, description="Años a evaluar (por defecto, backtest.default_years)")
    strategies: list[str] | None = Field(None, description="Subconjunto de estrategias; la combinada siempre se incluye")
    commission_pct: float | None = Field(None, ge=0, le=10)
    slippage_pct: float | None = Field(None, ge=0, le=10)
    buy_threshold: float | None = Field(None, ge=-100, le=100)
    sell_threshold: float | None = Field(None, ge=-100, le=100)
    weights: dict[str, float] | None = None


@router.post("/backtest")
async def backtest(body: BacktestIn, request: Request):
    symbol = _symbol(body.symbol)
    unknown = [s for s in (body.strategies or []) + list(body.weights or {}) if s not in STRATEGIES]
    if unknown:
        raise HTTPException(422, f"Estrategias desconocidas: {unknown}. Disponibles: {list(STRATEGIES)}")
    thresholds = {k: v for k, v in {"buy": body.buy_threshold, "sell": body.sell_threshold}.items() if v is not None}
    t = {**md(request).cfg.signals.thresholds.model_dump(), **thresholds}
    if t["sell"] >= t["buy"]:
        raise HTTPException(422, "El umbral de venta debe ser menor que el de compra")
    try:
        return await asyncio.to_thread(
            sig(request).backtest, symbol, body.years, body.strategies, body.commission_pct, body.slippage_pct,
            thresholds or None, body.weights,
        )
    except NoHistory as e:
        raise HTTPException(404, f"{e}. Agrégalo a la watchlist para descargar su historia.")
    except ValueError as e:
        raise HTTPException(422, str(e))
