"""Protecciones del bot: límites, pérdida diaria, spread, una operación por vela."""
from __future__ import annotations

import pandas as pd
import pytest

from bot_engine import DayState, load_bot_config, plan_entries, read_control, write_control
from brokers import AccountState, Position, SymbolSpec
from config import BotConfig
from data_fetcher import AssetData, MarketSnapshot
from indicators import SignalResult, TimeframeAnalysis

BAR = pd.Timestamp("2026-11-24T10:00:00Z")


def sig(key: str, score: float, atr: float = 10.0) -> SignalResult:
    tf = TimeframeAnalysis(tf="1h", ok=True, values={"atr": atr}, bar_time=BAR)
    return SignalResult(key=key, ok=True, score=score, verdict="X", timeframes={"1h": tf})


def snap(keys) -> MarketSnapshot:
    df = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [0.0]},
                      index=[BAR])
    assets = {k: AssetData(key=k, frames={"15m": df}, last_price=1.0) for k in keys}
    return MarketSnapshot(assets=assets, fetched_at=BAR, provider_requested="test")


def spec(key: str, spread: float = 0.2) -> SymbolSpec:
    return SymbolSpec(key, key, 2, 0.01, 100.0, 0.01, 0.01, 100.0, 0.0, 2650.0, 2650.0 + spread)


ACC = AccountState(10_000, 10_000, "USD", True)
CFG = BotConfig(entry_threshold=70, risk_pct=1.0)


def test_opens_strong_signal_with_correct_size() -> None:
    day = DayState(day="d", start_equity=10_000)
    out = plan_entries({"GOLD": sig("GOLD", 85)}, snap(["GOLD"]), [], ACC, spec, CFG, day)
    d = out[0]
    assert d.action == "OPEN" and d.direction == 1
    assert d.sl == pytest.approx(2650.2 - 15) and d.tp == pytest.approx(2650.2 + 30)
    assert d.lots == 0.06  # 100 USD / (15 × 100) = 0.0667 → 0.06


def test_below_threshold_ignored() -> None:
    assert plan_entries({"GOLD": sig("GOLD", 50)}, snap(["GOLD"]), [], ACC, spec, CFG, DayState()) == []


def test_correlated_index_limit() -> None:
    sigs = {k: sig(k, 90 - i) for i, k in enumerate(["US500", "US100", "US30"])}
    out = plan_entries(sigs, snap(sigs), [], ACC, spec, CFG, DayState(start_equity=10_000))
    assert [d.action for d in out] == ["OPEN", "OPEN", "SKIP"]
    assert "índices" in out[2].reason


def test_daily_loss_halts() -> None:
    day = DayState(day="d", start_equity=10_000)
    acc = AccountState(10_000, 9_700, "USD", True)  # −3 % > límite de 2 %
    out = plan_entries({"GOLD": sig("GOLD", 90)}, snap(["GOLD"]), [], acc, spec, BotConfig(), day)
    assert out[0].action == "SKIP" and "Pérdida diaria" in out[0].reason and day.halted_reason


def test_one_trade_per_bar_and_open_position() -> None:
    day = DayState(start_equity=10_000, traded_bars={"GOLD": BAR.isoformat()})
    out = plan_entries({"GOLD": sig("GOLD", 90)}, snap(["GOLD"]), [], ACC, spec, CFG, day)
    assert out[0].action == "SKIP" and "vela" in out[0].reason
    pos = [Position(1, "SILVER", "XAGUSD", 1, 0.1, 31, 30, 33)]
    out = plan_entries({"SILVER": sig("SILVER", 90)}, snap(["SILVER"]), pos, ACC, spec, CFG, DayState(start_equity=10_000))
    assert "posición abierta" in out[0].reason


def test_spread_and_stale_guards() -> None:
    out = plan_entries({"GOLD": sig("GOLD", 90, atr=1.0)}, snap(["GOLD"]), [], ACC, lambda k: spec(k, 0.5), CFG,
                       DayState(start_equity=10_000))
    assert "Spread" in out[0].reason
    s = snap(["GOLD"])
    s.assets["GOLD"].is_stale = True
    out = plan_entries({"GOLD": sig("GOLD", 90)}, s, [], ACC, spec, CFG, DayState(start_equity=10_000))
    assert "atrasados" in out[0].reason


def test_config_from_env_and_validation() -> None:
    cfg = load_bot_config({"BOT_MODE": "demo", "BOT_RISK_PCT": "0.25", "BOT_NOTIFY_TELEGRAM": "no"})
    assert cfg.mode == "demo" and cfg.risk_pct == 0.25 and cfg.notify_telegram is False
    with pytest.raises(ValueError):
        load_bot_config({"BOT_MODE": "yolo"})
    with pytest.raises(ValueError):
        load_bot_config({"BOT_RISK_PCT": "50"})


def test_control_file_roundtrip() -> None:
    assert read_control()["paused"] is False
    write_control(paused=True, emergency_close=True)
    ctl = read_control()
    assert ctl["paused"] and ctl["emergency_close"]
