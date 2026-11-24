"""
bot_engine.py — Lógica de decisión del bot (sin E/S de red: fácil de testear).

Responsabilidades:
  * Leer la configuración del bot desde .env (BOT_*).
  * Aplicar las protecciones antes de abrir cualquier operación.
  * Calcular lotaje, SL y TP con las especificaciones REALES del símbolo.
  * Leer/escribir los archivos de estado y control compartidos con la app.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
from dotenv import load_dotenv

import config
from brokers import AccountState, Position, SymbolSpec
from config import ASSETS, BotConfig
from data_fetcher import MarketSnapshot
from indicators import SignalResult
from risk_engine import calculate_lot_size

load_dotenv(config.BASE_DIR / ".env")


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
def load_bot_config(env: dict[str, str] | None = None) -> BotConfig:
    """Construye BotConfig con los valores por defecto sobrescritos por BOT_<CAMPO> en .env."""
    env = dict(os.environ) if env is None else env
    defaults = BotConfig()
    values: dict[str, Any] = {}
    for f in fields(BotConfig):
        raw = env.get(f"BOT_{f.name.upper()}")
        if raw is None or raw.strip() == "":
            continue
        current = getattr(defaults, f.name)
        try:
            if isinstance(current, bool):
                values[f.name] = raw.strip().lower() in ("1", "true", "si", "sí", "yes", "on")
            elif isinstance(current, int):
                values[f.name] = int(raw)
            elif isinstance(current, float):
                values[f.name] = float(raw)
            else:
                values[f.name] = raw.strip().lower()
        except ValueError as exc:
            raise ValueError(f"Valor inválido para BOT_{f.name.upper()}: {raw!r}") from exc
    cfg = BotConfig(**{**asdict(defaults), **values})
    validate_bot_config(cfg)
    return cfg


def validate_bot_config(cfg: BotConfig) -> None:
    errors: list[str] = []
    if cfg.mode not in config.BOT_MODES:
        errors.append(f"BOT_MODE debe ser uno de {config.BOT_MODES}")
    if not 0 < cfg.risk_pct <= config.MAX_RISK_PCT:
        errors.append(f"BOT_RISK_PCT debe estar entre 0 y {config.MAX_RISK_PCT}")
    if not 0 < cfg.entry_threshold <= 100:
        errors.append("BOT_ENTRY_THRESHOLD debe estar entre 1 y 100")
    if cfg.sl_atr_mult <= 0 or cfg.tp_atr_mult <= 0:
        errors.append("Los multiplicadores de ATR deben ser > 0")
    if cfg.atr_tf not in config.TIMEFRAMES:
        errors.append(f"BOT_ATR_TF debe ser uno de {config.TIMEFRAMES}")
    if cfg.max_daily_loss_pct <= 0:
        errors.append("BOT_MAX_DAILY_LOSS_PCT debe ser > 0")
    if cfg.loop_seconds < 5:
        errors.append("BOT_LOOP_SECONDS mínimo 5")
    if cfg.data_source not in ("auto", "mt5", "feed"):
        errors.append("BOT_DATA_SOURCE debe ser auto, mt5 o feed")
    if errors:
        raise ValueError(" · ".join(errors))


# ---------------------------------------------------------------------------
# Estado diario
# ---------------------------------------------------------------------------
@dataclass
class DayState:
    day: str = ""
    start_equity: float = 0.0
    trades_today: int = 0
    traded_bars: dict[str, str] = field(default_factory=dict)   # activo → vela 1H en la que operó
    halted_reason: str = ""

    def roll(self, equity: float, today: str) -> bool:
        """Reinicia los contadores al cambiar de día. Devuelve True si hubo cambio."""
        if self.day != today:
            self.day, self.start_equity, self.trades_today, self.halted_reason = today, equity, 0, ""
            return True
        return False

    def daily_pnl_pct(self, equity: float) -> float:
        if self.start_equity <= 0:
            return 0.0
        return (equity / self.start_equity - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Decisiones
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    key: str
    action: str                 # "OPEN" o "SKIP"
    direction: int
    score: float
    reason: str
    bar_time: str = ""
    lots: float = 0.0
    entry: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    risk_amount: float = 0.0
    symbol: str = ""
    digits: int = 2


def daily_guard(account: AccountState, day: DayState, cfg: BotConfig) -> str | None:
    """Motivo para no abrir nada hoy, o None si se puede operar."""
    if day.halted_reason:
        return day.halted_reason
    pnl = day.daily_pnl_pct(account.equity)
    if pnl <= -cfg.max_daily_loss_pct:
        day.halted_reason = (f"Pérdida diaria {pnl:.2f}% alcanzó el límite de -{cfg.max_daily_loss_pct:.2f}%: "
                             "sin nuevas entradas hasta mañana")
        return day.halted_reason
    if day.trades_today >= cfg.max_trades_per_day:
        return f"Máximo de {cfg.max_trades_per_day} operaciones diarias alcanzado"
    if account.equity <= 0:
        return "Equity nulo o negativo"
    return None


def plan_entries(signals: dict[str, SignalResult], snapshot: MarketSnapshot, positions: list[Position],
                 account: AccountState, spec_fn: Callable[[str], SymbolSpec | None], cfg: BotConfig,
                 day: DayState) -> list[Decision]:
    """Evalúa todas las señales y devuelve qué abrir y qué se descarta (con el motivo)."""
    decisions: list[Decision] = []
    candidates = sorted(
        (s for s in signals.values() if s.ok and abs(s.score) >= cfg.entry_threshold),
        key=lambda s: abs(s.score), reverse=True,
    )
    if not candidates:
        return decisions

    open_keys = {p.key for p in positions}
    n_total = len(positions)
    n_index = sum(1 for p in positions if ASSETS[p.key].category == "index")
    n_metal = sum(1 for p in positions if ASSETS[p.key].category == "metal")
    block = daily_guard(account, day, cfg)
    risk_amount = account.equity * cfg.risk_pct / 100.0

    for sig in candidates:
        key = sig.key
        direction = 1 if sig.score > 0 else -1
        bar = sig.signal_bar_time()
        bar_str = bar.isoformat() if bar is not None else ""
        cat = ASSETS[key].category

        def skip(reason: str, _key: str = key, _d: int = direction, _s: float = sig.score,
                 _b: str = bar_str) -> None:
            decisions.append(Decision(_key, "SKIP", _d, _s, reason, _b))

        if block:
            skip(block)
            continue
        if bar_str and day.traded_bars.get(key) == bar_str:
            skip("Ya se operó este activo en esta vela de 1H")
            continue
        if key in open_keys:
            skip("Ya hay una posición abierta en este activo")
            continue
        if n_total >= cfg.max_open_positions:
            skip(f"Máximo de {cfg.max_open_positions} posiciones abiertas")
            continue
        if cat == "index" and n_index >= cfg.max_index_positions:
            skip(f"Máximo de {cfg.max_index_positions} índices a la vez (están correlacionados)")
            continue
        if cat == "metal" and n_metal >= cfg.max_metal_positions:
            skip(f"Máximo de {cfg.max_metal_positions} metal(es) a la vez")
            continue
        if day.trades_today + sum(1 for d in decisions if d.action == "OPEN") >= cfg.max_trades_per_day:
            skip(f"Máximo de {cfg.max_trades_per_day} operaciones diarias alcanzado")
            continue
        data = snapshot.assets.get(key)
        if data is None or not data.ok or data.is_stale:
            skip("Datos atrasados o mercado cerrado")
            continue
        spec = spec_fn(key)
        if spec is None:
            skip("Sin cotización del bróker")
            continue
        if not spec.market_open:
            skip("Mercado cerrado (sin ticks recientes)")
            continue
        atr = sig.atr(cfg.atr_tf)
        if atr is None or not np.isfinite(atr) or atr <= 0:
            skip("ATR no disponible")
            continue
        if spec.spread > cfg.max_spread_atr_ratio * atr:
            skip(f"Spread {spec.spread:.{spec.digits}f} demasiado alto (> {cfg.max_spread_atr_ratio:.0%} del ATR)")
            continue

        entry = spec.ask if direction > 0 else spec.bid
        sl_dist, tp_dist = cfg.sl_atr_mult * atr, cfg.tp_atr_mult * atr
        if sl_dist < spec.stops_level or tp_dist < spec.stops_level:
            skip("SL/TP más cerca que la distancia mínima del bróker")
            continue
        sl = round(entry - direction * sl_dist, spec.digits)
        tp = round(entry + direction * tp_dist, spec.digits)
        if sl <= 0:
            skip("Stop Loss en precio negativo")
            continue
        lot = calculate_lot_size(risk_amount, abs(entry - sl), spec.point_value,
                                 spec.min_lot, spec.lot_step, spec.max_lot)
        if lot.lots <= 0:
            skip(lot.warnings[0] if lot.warnings else "Lotaje 0")
            continue

        decisions.append(Decision(key, "OPEN", direction, sig.score,
                                  f"Score {sig.score:+.0f} ≥ umbral {cfg.entry_threshold:.0f} ({sig.verdict})",
                                  bar_str, lot.lots, entry, sl, tp, lot.risk_usd_real, spec.symbol, spec.digits))
        open_keys.add(key)
        n_total += 1
        n_index += cat == "index"
        n_metal += cat == "metal"
    return decisions


# ---------------------------------------------------------------------------
# Archivos compartidos con la app (estado / control / historial)
# ---------------------------------------------------------------------------
def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default)
    except (OSError, ValueError):
        return dict(default)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


DEFAULT_CONTROL: dict[str, Any] = {"paused": False, "emergency_close": False, "updated_at": ""}


def read_control() -> dict[str, Any]:
    return {**DEFAULT_CONTROL, **read_json(config.BOT_CONTROL_FILE, DEFAULT_CONTROL)}


def write_control(**changes: Any) -> dict[str, Any]:
    ctl = {**read_control(), **changes, "updated_at": datetime.now(timezone.utc).isoformat()}
    write_json(config.BOT_CONTROL_FILE, ctl)
    return ctl


def read_state() -> dict[str, Any]:
    return read_json(config.BOT_STATE_FILE, {})


def append_log(event: dict[str, Any]) -> None:
    config.BOT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    event = {"time": datetime.now(timezone.utc).isoformat(), **event}
    with config.BOT_LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, default=str, ensure_ascii=False) + "\n")


def read_log(limit: int = 200) -> list[dict[str, Any]]:
    if not config.BOT_LOG_FILE.exists():
        return []
    try:
        lines = config.BOT_LOG_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def heartbeat_age_s(state: dict[str, Any]) -> float | None:
    hb = state.get("heartbeat")
    if not hb:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(hb)).total_seconds()
    except ValueError:
        return None
