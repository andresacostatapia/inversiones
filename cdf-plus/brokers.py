"""
brokers.py — Capa de ejecución: bróker de papel (simulado) y MetaTrader 5.

Ambos exponen la misma interfaz, de modo que el bot no distingue entre ellos:

    connect() · account() · symbol_spec(key) · positions() · market_order(...)
    close_position(pos) · update(snapshot) · fetch_snapshot() · shutdown()

MetaTrader 5:
  * Requiere Windows, el terminal MT5 instalado y con sesión iniciada, y el paquete
    `MetaTrader5` (pip install MetaTrader5).
  * En MT5 activa el botón "Algo Trading" (si está apagado, el terminal rechaza las órdenes).
  * Las especificaciones (valor del tick, lote mínimo, decimales…) se leen del bróker, así que
    el lotaje es exacto para tu cuenta. No hace falta calibrar offsets: se opera con sus precios.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from types import ModuleType
from typing import Any, Protocol

import numpy as np
import pandas as pd
from dotenv import load_dotenv

import config
from config import ASSETS
from data_fetcher import AssetData, MarketSnapshot, clean_ohlcv, _daily_change

load_dotenv(config.BASE_DIR / ".env")
logger = logging.getLogger("cdfplus.brokers")


class BrokerError(Exception):
    """Error que impide operar (conexión, cuenta incorrecta, permisos…)."""


# ---------------------------------------------------------------------------
# Estructuras comunes
# ---------------------------------------------------------------------------
@dataclass
class AccountState:
    balance: float
    equity: float
    currency: str
    is_demo: bool
    server: str = ""
    login: str = ""
    leverage: int = 0
    trade_allowed: bool = True


@dataclass
class SymbolSpec:
    key: str
    symbol: str
    digits: int
    point: float              # Mínimo movimiento de precio
    point_value: float        # Moneda de la cuenta por 1.0 de precio con 1.0 lote
    min_lot: float
    lot_step: float
    max_lot: float
    stops_level: float        # Distancia mínima de SL/TP al precio (en precio)
    bid: float
    ask: float
    market_open: bool = True

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)

    @property
    def mid(self) -> float:
        return (self.ask + self.bid) / 2.0


@dataclass
class Position:
    ticket: int
    key: str
    symbol: str
    direction: int            # +1 compra, −1 venta
    volume: float
    open_price: float
    sl: float
    tp: float
    profit: float = 0.0       # Beneficio flotante en moneda de la cuenta
    open_time: str = ""
    comment: str = ""


@dataclass
class OrderResult:
    ok: bool
    message: str
    ticket: int | None = None
    price: float | None = None
    volume: float | None = None


class Broker(Protocol):
    name: str
    mode: str

    def connect(self) -> None: ...
    def account(self) -> AccountState: ...
    def symbol_spec(self, key: str) -> SymbolSpec | None: ...
    def positions(self) -> list[Position]: ...
    def market_order(self, key: str, direction: int, volume: float, sl: float, tp: float,
                     comment: str = "") -> OrderResult: ...
    def close_position(self, pos: Position) -> OrderResult: ...
    def update(self, snapshot: MarketSnapshot) -> list[dict[str, Any]]: ...
    def shutdown(self) -> None: ...


def _round_price(value: float, digits: int) -> float:
    return round(float(value), digits)


# ---------------------------------------------------------------------------
# MetaTrader 5
# ---------------------------------------------------------------------------
def _normalize(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def resolve_symbol(key: str, available: list[str]) -> str | None:
    """
    Encuentra el nombre del símbolo del bróker para un activo.

    1. Variable de entorno MT5_SYMBOL_<KEY> (si existe en el bróker).
    2. Coincidencia exacta normalizada con algún alias (XAUUSD, US500…).
    3. Alias + sufijo corto del bróker (US500.cash, XAUUSDm, NAS100+…).
    """
    override = os.getenv(f"MT5_SYMBOL_{key}", "").strip()
    if override:
        return override if override in available else None
    norm = {_normalize(s): s for s in available}
    aliases = config.MT5_SYMBOL_ALIASES.get(key, ())
    for alias in aliases:
        if alias in norm:
            return norm[alias]
    best: tuple[int, str] | None = None
    for alias in aliases:
        for n, original in norm.items():
            if n.startswith(alias) and len(n) - len(alias) <= 4:
                extra = len(n) - len(alias)
                if best is None or extra < best[0]:
                    best = (extra, original)
        if best:
            return best[1]
    return None


class MT5Broker:
    """Conector de MetaTrader 5 (cuenta demo o real)."""

    name = "MetaTrader 5"

    TF_MAP = {"15m": "TIMEFRAME_M15", "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4"}

    def __init__(self, mode: str = "demo", mt5_module: ModuleType | None = None) -> None:
        if mode not in ("demo", "real", "paper"):
            raise ValueError(f"Modo desconocido: {mode}")
        self.mode = mode
        self._mt5: Any = mt5_module
        self.symbols: dict[str, str] = {}
        self.missing: list[str] = []
        self._tick_seen: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        self.connected = False

    # --- conexión -----------------------------------------------------------
    @property
    def mt5(self) -> Any:
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5_mod  # type: ignore[import-not-found]
            except ImportError as exc:
                raise BrokerError(
                    "Falta el paquete MetaTrader5 (solo Windows): pip install MetaTrader5"
                ) from exc
            self._mt5 = mt5_mod
        return self._mt5

    def _last_error(self) -> str:
        try:
            code, msg = self.mt5.last_error()
            return f"[{code}] {msg}"
        except Exception:
            return "error desconocido"

    def connect(self) -> None:
        mt5 = self.mt5
        kwargs: dict[str, Any] = {}
        path = os.getenv("MT5_PATH", "").strip()
        login = os.getenv("MT5_LOGIN", "").strip()
        if login:
            if not login.isdigit():
                raise BrokerError("MT5_LOGIN debe ser el número de cuenta")
            kwargs.update(login=int(login), password=os.getenv("MT5_PASSWORD", ""),
                          server=os.getenv("MT5_SERVER", ""))
        ok = mt5.initialize(path, **kwargs) if path else mt5.initialize(**kwargs)
        if not ok:
            raise BrokerError(f"No se pudo conectar con MetaTrader 5 {self._last_error()}. "
                              "¿Está el terminal instalado y con sesión iniciada?")
        acc = mt5.account_info()
        if acc is None:
            raise BrokerError(f"MT5 sin cuenta activa {self._last_error()}")
        is_demo = acc.trade_mode in (mt5.ACCOUNT_TRADE_MODE_DEMO, mt5.ACCOUNT_TRADE_MODE_CONTEST)

        # Salvaguardas de modo
        if self.mode == "demo" and not is_demo:
            mt5.shutdown()
            raise BrokerError("Modo DEMO pero la cuenta conectada es REAL. No se operará.")
        if self.mode == "real":
            if is_demo:
                mt5.shutdown()
                raise BrokerError("Modo REAL pero la cuenta conectada es DEMO. Cambia BOT_MODE a demo.")
            if os.getenv("BOT_CONFIRM_REAL", "") != config.BOT_REAL_CONFIRMATION:
                mt5.shutdown()
                raise BrokerError(f"Para operar en REAL añade BOT_CONFIRM_REAL={config.BOT_REAL_CONFIRMATION} al .env")
        if self.mode in ("demo", "real"):
            term = mt5.terminal_info()
            if term is not None and not term.trade_allowed:
                mt5.shutdown()
                raise BrokerError("El botón 'Algo Trading' de MT5 está desactivado. Actívalo y reinicia el bot.")
            if not getattr(acc, "trade_expert", True):
                mt5.shutdown()
                raise BrokerError("La cuenta no permite trading automático (trade_expert = False).")

        available = [s.name for s in (mt5.symbols_get() or [])]
        self.symbols, self.missing = {}, []
        for key in ASSETS:
            sym = resolve_symbol(key, available)
            if sym and mt5.symbol_select(sym, True):
                self.symbols[key] = sym
            else:
                self.missing.append(key)
        if not self.symbols:
            mt5.shutdown()
            raise BrokerError("Ningún activo encontrado en el bróker. Define MT5_SYMBOL_<ACTIVO> en .env")
        self.connected = True
        logger.info("MT5 conectado: %s · símbolos %s · faltan %s", acc.server, self.symbols, self.missing)

    def shutdown(self) -> None:
        if self._mt5 is not None and self.connected:
            self.mt5.shutdown()
        self.connected = False

    # --- cuenta y símbolos -----------------------------------------------------
    def account(self) -> AccountState:
        mt5 = self.mt5
        acc = mt5.account_info()
        if acc is None:
            raise BrokerError(f"No se pudo leer la cuenta {self._last_error()}")
        term = mt5.terminal_info()
        return AccountState(
            balance=float(acc.balance), equity=float(acc.equity), currency=str(acc.currency),
            is_demo=acc.trade_mode in (mt5.ACCOUNT_TRADE_MODE_DEMO, mt5.ACCOUNT_TRADE_MODE_CONTEST),
            server=str(acc.server), login=str(acc.login), leverage=int(acc.leverage),
            trade_allowed=bool(term.trade_allowed) if term is not None else True,
        )

    def _is_fresh(self, sym: str, tick_time: int) -> bool:
        """El mercado se considera abierto si el tick ha cambiado hace poco (reloj real)."""
        now = time.time()
        with self._lock:
            prev = self._tick_seen.get(sym)
            if prev is None:
                # Primera observación: se confirma en cuanto llegue un tick nuevo
                self._tick_seen[sym] = (tick_time, now - config.MT5_MAX_TICK_AGE_S - 1)
                return False
            if tick_time != prev[0]:
                self._tick_seen[sym] = (tick_time, now)
                return True
            return now - prev[1] <= config.MT5_MAX_TICK_AGE_S

    def symbol_spec(self, key: str) -> SymbolSpec | None:
        sym = self.symbols.get(key)
        if sym is None:
            return None
        mt5 = self.mt5
        info = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)
        if info is None or tick is None or tick.bid <= 0 or tick.ask <= 0:
            return None
        tick_size = float(info.trade_tick_size) or float(info.point)
        tick_value = float(info.trade_tick_value)
        if tick_size <= 0 or tick_value <= 0:
            return None
        return SymbolSpec(
            key=key, symbol=sym, digits=int(info.digits), point=float(info.point),
            point_value=tick_value / tick_size,
            min_lot=float(info.volume_min), lot_step=float(info.volume_step), max_lot=float(info.volume_max),
            stops_level=float(info.trade_stops_level) * float(info.point),
            bid=float(tick.bid), ask=float(tick.ask),
            market_open=self._is_fresh(sym, int(tick.time)) and info.trade_mode != mt5.SYMBOL_TRADE_MODE_DISABLED,
        )

    # --- datos de mercado -------------------------------------------------------
    def fetch_snapshot(self) -> MarketSnapshot:
        """Velas 15m/1h/4h de los 6 activos directamente del bróker (precios = los que se operan)."""
        mt5 = self.mt5
        assets: dict[str, AssetData] = {}
        for key in ASSETS:
            sym = self.symbols.get(key)
            if sym is None:
                assets[key] = AssetData(key=key, provider="mt5", error="Símbolo no disponible en el bróker")
                continue
            try:
                frames: dict[str, pd.DataFrame] = {}
                for tf, attr in self.TF_MAP.items():
                    rates = mt5.copy_rates_from_pos(sym, getattr(mt5, attr), 0, config.MT5_BARS[tf])
                    if rates is None or len(rates) == 0:
                        raise BrokerError(f"Sin velas {tf} {self._last_error()}")
                    df = pd.DataFrame(rates)
                    df.index = pd.to_datetime(df["time"], unit="s", utc=True)  # Hora del servidor
                    df = df.rename(columns={"tick_volume": "volume"})
                    frames[tf] = clean_ohlcv(df[["open", "high", "low", "close", "volume"]])
                tick = mt5.symbol_info_tick(sym)
                data = AssetData(key=key, provider="mt5", feed_symbol=sym, scale=1.0, frames=frames)
                data.last_price = float((tick.bid + tick.ask) / 2) if tick else float(frames["15m"]["close"].iloc[-1])
                data.raw_last_price = data.last_price
                data.last_bar_time = frames["15m"].index[-1]
                data.prev_close, data.change_pct = _daily_change(frames["15m"])
                data.is_stale = not self._is_fresh(sym, int(tick.time)) if tick else True
                if data.is_stale:
                    data.warnings.append("Sin ticks recientes: mercado cerrado o sin conexión.")
                assets[key] = data
            except Exception as exc:
                assets[key] = AssetData(key=key, provider="mt5", feed_symbol=sym, error=str(exc))
        return MarketSnapshot(assets=assets, fetched_at=datetime.now(timezone.utc), provider_requested="mt5")

    def server_time(self) -> pd.Timestamp | None:
        """Hora del servidor según el tick más reciente (para descartar velas sin cerrar)."""
        times = []
        for sym in self.symbols.values():
            tick = self.mt5.symbol_info_tick(sym)
            if tick is not None:
                times.append(int(tick.time))
        return pd.Timestamp(max(times), unit="s", tz="UTC") if times else None

    # --- órdenes ----------------------------------------------------------------
    def _filling(self, sym: str) -> int:
        mt5 = self.mt5
        info = mt5.symbol_info(sym)
        mode = int(getattr(info, "filling_mode", 0)) if info else 0
        if mode & 1:
            return mt5.ORDER_FILLING_FOK
        if mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _send(self, request: dict[str, Any]) -> OrderResult:
        mt5 = self.mt5
        check = mt5.order_check(request)
        if check is None:
            return OrderResult(False, f"order_check falló {self._last_error()}")
        if check.retcode != 0:
            return OrderResult(False, f"Orden rechazada en la verificación [{check.retcode}] {check.comment}")
        res = mt5.order_send(request)
        if res is None:
            return OrderResult(False, f"order_send falló {self._last_error()}")
        ok_codes = {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_DONE_PARTIAL}
        if res.retcode not in ok_codes:
            return OrderResult(False, f"Orden rechazada por el bróker [{res.retcode}] {res.comment}")
        return OrderResult(True, f"Ejecutada [{res.retcode}] {res.comment}", ticket=int(res.order),
                           price=float(res.price), volume=float(res.volume))

    def market_order(self, key: str, direction: int, volume: float, sl: float, tp: float,
                     comment: str = "") -> OrderResult:
        if self.mode == "paper":
            return OrderResult(False, "MT5Broker en modo papel no envía órdenes")
        mt5 = self.mt5
        spec = self.symbol_spec(key)
        if spec is None:
            return OrderResult(False, f"{key}: símbolo sin cotización")
        buy = direction > 0
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": spec.symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
            "price": spec.ask if buy else spec.bid,
            "sl": _round_price(sl, spec.digits),
            "tp": _round_price(tp, spec.digits),
            "deviation": config.MT5_DEVIATION_POINTS,
            "magic": config.MT5_MAGIC,
            "comment": (comment or "CDFPlus")[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(spec.symbol),
        }
        return self._send(request)

    def positions(self) -> list[Position]:
        mt5 = self.mt5
        raw = mt5.positions_get() or ()
        by_symbol = {v: k for k, v in self.symbols.items()}
        out: list[Position] = []
        for p in raw:
            if int(p.magic) != config.MT5_MAGIC or p.symbol not in by_symbol:
                continue
            out.append(Position(
                ticket=int(p.ticket), key=by_symbol[p.symbol], symbol=p.symbol,
                direction=1 if p.type == mt5.POSITION_TYPE_BUY else -1, volume=float(p.volume),
                open_price=float(p.price_open), sl=float(p.sl), tp=float(p.tp), profit=float(p.profit),
                open_time=datetime.fromtimestamp(int(p.time), timezone.utc).isoformat(), comment=str(p.comment),
            ))
        return out

    def close_position(self, pos: Position) -> OrderResult:
        if self.mode == "paper":
            return OrderResult(False, "MT5Broker en modo papel no cierra posiciones")
        mt5 = self.mt5
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return OrderResult(False, f"{pos.symbol}: sin cotización para cerrar")
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if pos.direction > 0 else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price": tick.bid if pos.direction > 0 else tick.ask,
            "deviation": config.MT5_DEVIATION_POINTS,
            "magic": config.MT5_MAGIC,
            "comment": "CDFPlus cierre",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(pos.symbol),
        }
        return self._send(request)

    def update(self, snapshot: MarketSnapshot) -> list[dict[str, Any]]:
        return []  # En MT5 el propio servidor ejecuta SL/TP

    def closed_profit(self, ticket: int) -> float | None:
        """Resultado neto (beneficio + comisión + swap) de una posición ya cerrada."""
        try:
            deals = self.mt5.history_deals_get(position=ticket)
        except Exception:
            return None
        if not deals:
            return None
        return float(sum(float(d.profit) + float(getattr(d, "commission", 0.0)) + float(getattr(d, "swap", 0.0))
                         for d in deals))


# ---------------------------------------------------------------------------
# Bróker de papel (simulación con precios reales)
# ---------------------------------------------------------------------------
@dataclass
class _PaperState:
    balance: float
    positions: list[dict[str, Any]] = field(default_factory=list)
    closed: list[dict[str, Any]] = field(default_factory=list)
    next_ticket: int = 1
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PaperBroker:
    """
    Simula la ejecución: abre al ask/bid, cierra por SL/TP revisando las velas de 15m.

    Si recibe `price_source` (un MT5Broker conectado), usa los precios y especificaciones
    reales del bróker; si no, usa el feed (Twelve Data/yfinance) y los contratos de config.py.
    """

    name = "Papel (simulado)"
    mode = "paper"

    def __init__(self, initial_balance: float = 10_000.0, path: Any = None,
                 price_source: MT5Broker | None = None) -> None:
        self.path = path if path is not None else config.PAPER_ACCOUNT_FILE
        self.price_source = price_source
        self.initial_balance = initial_balance
        self._snapshot: MarketSnapshot | None = None
        self.state = self._load()

    # --- persistencia -------------------------------------------------------------
    def _load(self) -> _PaperState:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                return _PaperState(**raw)
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("Cuenta de papel ilegible (%s): se crea una nueva", exc)
        return _PaperState(balance=self.initial_balance)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.state), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def reset(self) -> None:
        self.state = _PaperState(balance=self.initial_balance)
        self._save()

    # --- interfaz ---------------------------------------------------------------
    def connect(self) -> None:
        self._save()

    def shutdown(self) -> None:
        if self.price_source is not None:
            self.price_source.shutdown()

    def symbol_spec(self, key: str) -> SymbolSpec | None:
        if self.price_source is not None:
            return self.price_source.symbol_spec(key)
        snap = self._snapshot
        data = snap.assets.get(key) if snap else None
        if data is None or not data.ok or data.last_price is None:
            return None
        a = ASSETS[key]
        half = config.TYPICAL_SPREAD.get(key, 0.0) / 2.0
        return SymbolSpec(
            key=key, symbol=data.feed_symbol, digits=a.digits, point=10 ** -a.digits,
            point_value=a.point_value_usd, min_lot=a.min_lot, lot_step=a.lot_step, max_lot=a.max_lot,
            stops_level=0.0, bid=data.last_price - half, ask=data.last_price + half,
            market_open=not data.is_stale,
        )

    def _floating(self, p: dict[str, Any]) -> float:
        spec = self.symbol_spec(p["key"])
        if spec is None:
            return float(p.get("profit", 0.0))
        exit_price = spec.bid if p["direction"] > 0 else spec.ask
        return (exit_price - p["open_price"]) * p["direction"] * p["volume"] * p["point_value"]

    def account(self) -> AccountState:
        floating = 0.0
        for p in self.state.positions:
            p["profit"] = self._floating(p)
            floating += p["profit"]
        currency = "USD"
        if self.price_source is not None and self.price_source.connected:
            try:
                currency = self.price_source.account().currency
            except BrokerError:
                pass
        return AccountState(balance=self.state.balance, equity=self.state.balance + floating,
                            currency=currency, is_demo=True, server="papel", login="papel")

    def positions(self) -> list[Position]:
        return [Position(ticket=p["ticket"], key=p["key"], symbol=p["symbol"], direction=p["direction"],
                         volume=p["volume"], open_price=p["open_price"], sl=p["sl"], tp=p["tp"],
                         profit=self._floating(p), open_time=p["open_time"], comment=p.get("comment", ""))
                for p in self.state.positions]

    def market_order(self, key: str, direction: int, volume: float, sl: float, tp: float,
                     comment: str = "") -> OrderResult:
        spec = self.symbol_spec(key)
        if spec is None:
            return OrderResult(False, f"{key}: sin precio para simular")
        if volume < spec.min_lot:
            return OrderResult(False, "Volumen inferior al mínimo")
        price = spec.ask if direction > 0 else spec.bid
        ticket = self.state.next_ticket
        self.state.next_ticket += 1
        snap_data = self._snapshot.assets.get(key) if self._snapshot else None
        bar_time = snap_data.frames["15m"].index[-1].isoformat() if snap_data and snap_data.ok else ""
        self.state.positions.append({
            "ticket": ticket, "key": key, "symbol": spec.symbol, "direction": direction,
            "volume": float(volume), "open_price": price, "sl": float(sl), "tp": float(tp),
            "point_value": spec.point_value, "open_time": datetime.now(timezone.utc).isoformat(),
            "checked_until": bar_time, "comment": comment, "profit": 0.0,
        })
        self._save()
        return OrderResult(True, "Ejecutada (simulada)", ticket=ticket, price=price, volume=volume)

    def _close(self, p: dict[str, Any], price: float, reason: str) -> dict[str, Any]:
        pnl = (price - p["open_price"]) * p["direction"] * p["volume"] * p["point_value"]
        self.state.balance += pnl
        self.state.positions = [x for x in self.state.positions if x["ticket"] != p["ticket"]]
        record = {**p, "close_price": price, "close_time": datetime.now(timezone.utc).isoformat(),
                  "pnl": pnl, "reason": reason}
        self.state.closed.append(record)
        self.state.closed = self.state.closed[-500:]
        self._save()
        return record

    def close_position(self, pos: Position) -> OrderResult:
        p = next((x for x in self.state.positions if x["ticket"] == pos.ticket), None)
        if p is None:
            return OrderResult(False, "Posición no encontrada")
        spec = self.symbol_spec(p["key"])
        if spec is None:
            return OrderResult(False, "Sin precio para cerrar")
        price = spec.bid if p["direction"] > 0 else spec.ask
        rec = self._close(p, price, "manual")
        return OrderResult(True, f"Cerrada (simulada) P/L {rec['pnl']:+.2f}", ticket=pos.ticket, price=price)

    def update(self, snapshot: MarketSnapshot) -> list[dict[str, Any]]:
        """Recibe datos nuevos y cierra posiciones cuyo SL/TP se haya tocado."""
        self._snapshot = snapshot
        closed: list[dict[str, Any]] = []
        for p in list(self.state.positions):
            data = snapshot.assets.get(p["key"])
            if data is None or not data.ok:
                continue
            d, sl, tp = p["direction"], p["sl"], p["tp"]
            # 1) Precio actual (detecta toques dentro de la vela en curso)
            spec = self.symbol_spec(p["key"])
            if spec is not None:
                px = spec.bid if d > 0 else spec.ask
                if (d > 0 and px <= sl) or (d < 0 and px >= sl):
                    closed.append(self._close(p, px, "SL"))
                    continue
                if (d > 0 and px >= tp) or (d < 0 and px <= tp):
                    closed.append(self._close(p, px, "TP"))
                    continue
            # 2) Velas de 15m posteriores a la última revisión (detecta toques entre ciclos)
            all_bars = data.frames["15m"]
            last_ts = all_bars.index[-1]
            bars = all_bars
            if p.get("checked_until"):
                bars = bars[bars.index > pd.Timestamp(p["checked_until"])]
            for ts, bar in bars.iterrows():
                o, h, low = float(bar["open"]), float(bar["high"]), float(bar["low"])
                exit_px, reason = None, ""
                if d > 0:
                    if o <= sl or o >= tp:
                        exit_px, reason = o, "gap SL" if o <= sl else "gap TP"
                    elif low <= sl:
                        exit_px, reason = sl, "SL"
                    elif h >= tp:
                        exit_px, reason = tp, "TP"
                else:
                    if o >= sl or o <= tp:
                        exit_px, reason = o, "gap SL" if o >= sl else "gap TP"
                    elif h >= sl:
                        exit_px, reason = sl, "SL"
                    elif low <= tp:
                        exit_px, reason = tp, "TP"
                if exit_px is not None:
                    closed.append(self._close(p, exit_px, reason))
                    break
                if ts != last_ts:  # La vela en curso se vuelve a revisar en el siguiente ciclo
                    p["checked_until"] = pd.Timestamp(ts).isoformat()
        self._save()
        return closed

    def fetch_snapshot(self) -> MarketSnapshot:
        if self.price_source is not None:
            return self.price_source.fetch_snapshot()
        raise BrokerError("PaperBroker sin fuente de precios propia: usa data_fetcher")


# ---------------------------------------------------------------------------
# Fábrica
# ---------------------------------------------------------------------------
def mt5_available() -> bool:
    try:
        import MetaTrader5  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


def make_broker(cfg: config.BotConfig) -> tuple[Broker, str]:
    """Crea el bróker según el modo y la fuente de datos. Devuelve (bróker, fuente de datos)."""
    if cfg.mode in ("demo", "real"):
        broker = MT5Broker(cfg.mode)
        broker.connect()
        return broker, "mt5"
    # Modo papel: precios de MT5 si se pide/está disponible; si no, del feed
    price_source: MT5Broker | None = None
    source = "feed"
    if cfg.data_source in ("auto", "mt5") and mt5_available():
        try:
            price_source = MT5Broker("paper")
            price_source.connect()
            source = "mt5"
        except BrokerError as exc:
            if cfg.data_source == "mt5":
                raise
            logger.warning("MT5 no disponible para el modo papel (%s); se usa el feed", exc)
            price_source = None
    elif cfg.data_source == "mt5":
        raise BrokerError("BOT_DATA_SOURCE=mt5 pero el paquete MetaTrader5 no está instalado")
    paper = PaperBroker(cfg.paper_balance, price_source=price_source)
    paper.connect()
    return paper, source


def finite(x: float | None) -> bool:
    return x is not None and bool(np.isfinite(x))
