"""Configuración común de los tests: rutas, estado aislado y datos sintéticos."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Cada test escribe su estado en un directorio temporal (nunca en .state real)."""
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "BOT_STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(config, "BOT_CONTROL_FILE", tmp_path / "bot_control.json")
    monkeypatch.setattr(config, "BOT_LOG_FILE", tmp_path / "bot_trades.jsonl")
    monkeypatch.setattr(config, "PAPER_ACCOUNT_FILE", tmp_path / "paper_account.json")
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TWELVE_DATA_API_KEY", "BOT_CONFIRM_REAL",
                "MT5_LOGIN", "MT5_PATH"):
        monkeypatch.delenv(var, raising=False)
    yield tmp_path


def make_ohlc(n: int, start: str = "2026-01-05", freq: str = "15min", seed: int = 0,
              drift: float = 0.0, base: float = 100.0, vol: float = 0.002) -> pd.DataFrame:
    """Serie OHLC sintética (paseo aleatorio log-normal) con índice UTC."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = base * np.exp(np.cumsum(rets))
    open_ = np.concatenate(([base], close[:-1]))
    spread = np.abs(rng.normal(0, vol / 2, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)
