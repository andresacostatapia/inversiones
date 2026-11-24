"""
data_fetcher.py — Obtención de datos OHLCV multi-temporalidad (15m, 1h, 4h).

Fuentes:
  1. Twelve Data (si existe TWELVE_DATA_API_KEY en .env).
     Se descarga UNA serie de 15 min por activo (1 crédito) y se remuestrea a 1h y 4h,
     lo que mantiene 6 activos dentro del límite gratuito de 8 peticiones/minuto.
  2. yfinance (fallback automático, sin API key). 15m y 1h directos; 4h remuestreado de 1h.

Si Twelve Data falla para un activo concreto (límite, símbolo no disponible en el plan…)
ese activo se reintenta automáticamente con yfinance.

Todas las series devueltas ya están multiplicadas por el factor de escala del activo, es
decir, expresadas en "puntos de índice" (p. ej. SPY×10 ≈ US500). El offset de xStation
se aplica después, en risk_engine.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

import config
from config import ASSETS, AssetConfig

load_dotenv(config.BASE_DIR / ".env")
logger = logging.getLogger("cdfplus.data")

PROVIDER_AUTO = "auto"
PROVIDER_TWELVE = "twelvedata"
PROVIDER_YF = "yfinance"

PROVIDER_LABELS: dict[str, str] = {
    PROVIDER_TWELVE: "Twelve Data",
    PROVIDER_YF: "yfinance",
}

OHLCV_COLUMNS: list[str] = ["open", "high", "low", "close", "volume"]


class DataFetchError(Exception):
    """Error controlado al obtener datos de un proveedor."""


# ---------------------------------------------------------------------------
# Estructuras de datos
# ---------------------------------------------------------------------------
@dataclass
class AssetData:
    """Datos de mercado de un activo en todas las temporalidades."""

    key: str
    provider: str = ""
    feed_symbol: str = ""
    scale: float = 1.0
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    last_price: float | None = None          # Precio escalado (sin offset XTB)
    raw_last_price: float | None = None      # Precio tal cual lo da el feed
    prev_close: float | None = None          # Cierre del día anterior (escalado)
    change_pct: float | None = None          # Variación diaria %
    last_bar_time: pd.Timestamp | None = None
    is_stale: bool = False                   # Último dato demasiado antiguo
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.frames) and self.last_price is not None


@dataclass
class MarketSnapshot:
    """Resultado de una actualización completa de los 6 activos."""

    assets: dict[str, AssetData]
    fetched_at: datetime
    provider_requested: str
    twelve_requests: int = 0

    @property
    def providers_used(self) -> set[str]:
        return {a.provider for a in self.assets.values() if a.ok}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def get_twelve_api_key() -> str | None:
    """Devuelve la API key de Twelve Data o None si no está configurada."""
    key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    if not key or key.lower() in {"tu_api_key", "your_api_key", "changeme"}:
        return None
    return key


def resolve_provider(preference: str) -> str:
    """Decide el proveedor efectivo según la preferencia del usuario y la API key."""
    if preference == PROVIDER_YF:
        return PROVIDER_YF
    if get_twelve_api_key():
        return PROVIDER_TWELVE
    return PROVIDER_YF


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza un DataFrame OHLCV: columnas, índice UTC, orden, duplicados y valores inválidos."""
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    if "volume" not in out.columns:
        out["volume"] = 0.0
    missing = [c for c in ("open", "high", "low", "close") if c not in out.columns]
    if missing:
        raise DataFetchError(f"Faltan columnas en los datos: {missing}")
    out = out[OHLCV_COLUMNS].apply(pd.to_numeric, errors="coerce")
    idx = pd.DatetimeIndex(out.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out.index = idx
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out["volume"] = out["volume"].fillna(0.0)
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]
    # Corrige velas incoherentes (high < max(open, close), etc.)
    out["high"] = out[["open", "high", "low", "close"]].max(axis=1)
    out["low"] = out[["open", "high", "low", "close"]].min(axis=1)
    return out


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Remuestrea OHLCV a una temporalidad mayor ('1h', '4h') alineada a UTC."""
    if df.empty:
        return df.copy()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df.resample(rule, label="left", closed="left").agg(agg)
    return out.dropna(subset=["open", "high", "low", "close"])


def apply_scale(df: pd.DataFrame, scale: float) -> pd.DataFrame:
    """Multiplica precios por el factor de escala (ETF → índice)."""
    if scale == 1.0 or df.empty:
        return df
    out = df.copy()
    out[["open", "high", "low", "close"]] = out[["open", "high", "low", "close"]] * scale
    return out


def _daily_change(df15: pd.DataFrame) -> tuple[float | None, float | None]:
    """Calcula el cierre del día UTC anterior y la variación % respecto al último precio."""
    if df15.empty:
        return None, None
    last_ts = df15.index[-1]
    previous = df15[df15.index.normalize() < last_ts.normalize()]
    if previous.empty:
        return None, None
    prev_close = float(previous["close"].iloc[-1])
    last = float(df15["close"].iloc[-1])
    if prev_close <= 0:
        return prev_close, None
    return prev_close, (last / prev_close - 1.0) * 100.0


def _finalize(asset: AssetConfig, provider: str, symbol: str, scale: float,
              base15: pd.DataFrame, base1h: pd.DataFrame | None) -> AssetData:
    """Construye AssetData a partir de las series base (ya limpias, sin escalar)."""
    if base15.empty:
        raise DataFetchError("El proveedor no devolvió velas (¿mercado cerrado o símbolo sin datos?)")

    raw_last = float(base15["close"].iloc[-1])
    df15 = apply_scale(base15, scale)
    df1h = apply_scale(base1h, scale) if base1h is not None and not base1h.empty else resample_ohlcv(df15, "1h")
    df4h = resample_ohlcv(df1h, "4h")
    frames = {"15m": df15, "1h": df1h, "4h": df4h}

    data = AssetData(key=asset.key, provider=provider, feed_symbol=symbol, scale=scale, frames=frames)
    data.raw_last_price = raw_last
    data.last_price = float(df15["close"].iloc[-1])
    last_bar_time = pd.Timestamp(df15.index[-1])
    data.last_bar_time = last_bar_time
    data.prev_close, data.change_pct = _daily_change(df15)

    age_min = (pd.Timestamp.now(tz="UTC") - last_bar_time).total_seconds() / 60.0
    data.is_stale = age_min > config.TF_MINUTES["15m"] * config.STALE_BARS_TOLERANCE
    if data.is_stale:
        hours = age_min / 60.0
        data.warnings.append(
            f"Último dato hace {hours:.1f} h: mercado cerrado o feed retrasado. "
            "Las señales usan la última sesión disponible."
        )
    for tf, frame in frames.items():
        if len(frame) < config.MIN_BARS:
            data.warnings.append(
                f"{tf}: solo {len(frame)} velas (< {config.MIN_BARS}); la EMA 200 puede ser poco fiable."
            )
    return data


# ---------------------------------------------------------------------------
# Twelve Data
# ---------------------------------------------------------------------------
class RateLimiter:
    """Limitador de ventana deslizante (thread-safe) para respetar N peticiones/minuto."""

    def __init__(self, max_calls: int, period_s: float = 60.0, max_wait_s: float = 75.0) -> None:
        self.max_calls = max(1, max_calls)
        self.period_s = period_s
        self.max_wait_s = max_wait_s
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        deadline = time.monotonic() + self.max_wait_s
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.period_s:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                wait = self.period_s - (now - self._calls[0]) + 0.05
            if time.monotonic() + wait > deadline:
                raise DataFetchError("Límite de peticiones por minuto de Twelve Data alcanzado")
            time.sleep(min(wait, 1.0))


_TWELVE_LIMITER = RateLimiter(config.TWELVE_MAX_REQUESTS_PER_MIN)
_TWELVE_COUNTER_LOCK = threading.Lock()
_TWELVE_COUNTER: dict[str, int] = {"requests": 0}


def _twelve_request(symbol: str, interval: str, api_key: str) -> pd.DataFrame:
    """Descarga una serie temporal de Twelve Data y la devuelve como DataFrame limpio."""
    _TWELVE_LIMITER.acquire()
    with _TWELVE_COUNTER_LOCK:
        _TWELVE_COUNTER["requests"] += 1
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": config.TWELVE_OUTPUTSIZE,
        "timezone": "UTC",
        "order": "asc",
        "apikey": api_key,
    }
    try:
        resp = requests.get(config.TWELVE_DATA_URL, params=params, timeout=config.TWELVE_TIMEOUT_S)
    except requests.RequestException as exc:
        raise DataFetchError(f"Error de red con Twelve Data: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise DataFetchError(f"Twelve Data respondió HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise DataFetchError("Respuesta de Twelve Data no es JSON válido") from exc
    if payload.get("status") == "error":
        code = payload.get("code", "?")
        msg = str(payload.get("message", "error desconocido"))[:160]
        raise DataFetchError(f"Twelve Data [{code}]: {msg}")
    values = payload.get("values") or []
    if not values:
        raise DataFetchError("Twelve Data no devolvió velas para este símbolo")
    df = pd.DataFrame(values)
    if "datetime" not in df.columns:
        raise DataFetchError("Formato inesperado en la respuesta de Twelve Data")
    df.index = pd.to_datetime(df.pop("datetime"), utc=True)
    return clean_ohlcv(df)


def fetch_asset_twelve(asset: AssetConfig, api_key: str) -> AssetData:
    """Obtiene un activo desde Twelve Data (15 min → 1h → 4h)."""
    base15 = _twelve_request(asset.twelve_symbol, "15min", api_key)
    return _finalize(asset, PROVIDER_TWELVE, asset.twelve_symbol, asset.twelve_scale, base15, None)


# ---------------------------------------------------------------------------
# yfinance
# ---------------------------------------------------------------------------
def _yf_history(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """Descarga historial desde yfinance con manejo de errores."""
    try:
        import yfinance as yf  # Import diferido: si falta la librería, el error es controlado
    except ImportError as exc:
        raise DataFetchError("yfinance no está instalado (pip install yfinance)") from exc
    try:
        df = yf.Ticker(symbol).history(
            period=period, interval=interval, auto_adjust=False, prepost=True, actions=False
        )
    except Exception as exc:  # yfinance lanza tipos variados (red, rate limit, JSON…)
        raise DataFetchError(f"yfinance falló ({exc.__class__.__name__}): {str(exc)[:120]}") from exc
    if df is None or df.empty:
        raise DataFetchError(f"yfinance no devolvió datos {interval} para {symbol}")
    return clean_ohlcv(df)


def fetch_asset_yf(asset: AssetConfig) -> AssetData:
    """Obtiene un activo desde yfinance (15m y 1h directos, 4h remuestreado)."""
    base15 = _yf_history(asset.yf_symbol, "15m", config.YF_PERIODS["15m"])
    try:
        base1h = _yf_history(asset.yf_symbol, "1h", config.YF_PERIODS["1h"])
    except DataFetchError:
        base1h = None  # Se remuestrea desde 15m como último recurso
    data = _finalize(asset, PROVIDER_YF, asset.yf_symbol, asset.yf_scale, base15, base1h)
    if base1h is None:
        data.warnings.append("1h remuestreado desde 15m (historial corto para 4h).")
    return data


# ---------------------------------------------------------------------------
# Orquestación con fallback
# ---------------------------------------------------------------------------
def fetch_asset(asset: AssetConfig, provider: str) -> AssetData:
    """Obtiene un activo con el proveedor indicado y fallback automático a yfinance."""
    twelve_error: str | None = None
    if provider == PROVIDER_TWELVE:
        api_key = get_twelve_api_key()
        if api_key:
            try:
                return fetch_asset_twelve(asset, api_key)
            except DataFetchError as exc:
                twelve_error = str(exc)
                logger.warning("Twelve Data falló para %s: %s", asset.key, exc)
        else:
            twelve_error = "Sin API key de Twelve Data"
    try:
        data = fetch_asset_yf(asset)
        if twelve_error:
            data.warnings.insert(0, f"Fallback a yfinance → {twelve_error}")
        return data
    except DataFetchError as exc:
        msg = str(exc) if not twelve_error else f"{twelve_error} | {exc}"
        return AssetData(key=asset.key, provider=provider, error=msg)
    except Exception as exc:  # Último cinturón de seguridad: nunca romper la app
        logger.exception("Error inesperado obteniendo %s", asset.key)
        return AssetData(key=asset.key, provider=provider, error=f"Error inesperado: {exc}")


def _fetch_all(provider: str) -> MarketSnapshot:
    """Descarga los 6 activos en paralelo."""
    start_count = _TWELVE_COUNTER["requests"]
    with ThreadPoolExecutor(max_workers=len(ASSETS)) as pool:
        futures = {k: pool.submit(fetch_asset, a, provider) for k, a in ASSETS.items()}
        assets = {k: f.result() for k, f in futures.items()}
    return MarketSnapshot(
        assets=assets,
        fetched_at=datetime.now(timezone.utc),
        provider_requested=provider,
        twelve_requests=_TWELVE_COUNTER["requests"] - start_count,
    )


@st.cache_data(ttl=config.TWELVE_CACHE_TTL_S, show_spinner=False, max_entries=4)
def _cached_twelve(cache_buster: int) -> MarketSnapshot:
    return _fetch_all(PROVIDER_TWELVE)


@st.cache_data(ttl=config.YF_CACHE_TTL_S, show_spinner=False, max_entries=4)
def _cached_yf(cache_buster: int) -> MarketSnapshot:
    return _fetch_all(PROVIDER_YF)


def get_market_snapshot(preference: str = PROVIDER_AUTO, cache_buster: int = 0) -> MarketSnapshot:
    """
    Punto de entrada principal. Devuelve los datos de los 6 activos usando caché con TTL.

    `cache_buster` permite forzar una descarga nueva sin esperar al TTL (botón "Actualizar").
    """
    provider = resolve_provider(preference)
    if provider == PROVIDER_TWELVE:
        return _cached_twelve(cache_buster)
    return _cached_yf(cache_buster)


def clear_cache() -> None:
    """Vacía la caché de datos de ambos proveedores."""
    _cached_twelve.clear()
    _cached_yf.clear()


def total_twelve_requests() -> int:
    """Peticiones a Twelve Data realizadas desde que arrancó el proceso."""
    return _TWELVE_COUNTER["requests"]


def frame_is_usable(df: pd.DataFrame | None) -> bool:
    """True si la serie tiene datos suficientes para calcular indicadores básicos."""
    return df is not None and not df.empty and len(df) >= 30 and bool(np.isfinite(df["close"].iloc[-1]))
