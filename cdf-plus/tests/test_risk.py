"""Lotaje y ticket: redondeo hacia abajo y protección ante divisiones por cero."""
from __future__ import annotations

import math

import pytest

import config
import risk_engine as r


def test_lot_rounds_down_and_never_exceeds_risk() -> None:
    res = r.calculate_lot_size(100, 346.8, 20)  # US100: 0.0144 → 0.01
    assert res.lots == 0.01
    assert res.risk_usd_real <= 100


@pytest.mark.parametrize("args", [(100, 0, 20), (100, 10, 0), (0, 10, 20), (math.nan, 10, 20), (100, -5, 20)])
def test_lot_invalid_inputs(args) -> None:
    res = r.calculate_lot_size(*args)
    assert res.lots == 0 and res.warnings


def test_lot_below_minimum_explains() -> None:
    res = r.calculate_lot_size(10, 500, 20)
    assert res.lots == 0 and "lote mínimo" in res.warnings[0]


def test_floor_to_step() -> None:
    assert r.floor_to_step(0.0299999, 0.01) == 0.02
    assert r.floor_to_step(0.03, 0.01) == 0.03
    assert r.floor_to_step(1.27, 0.1) == 1.2


def test_ticket_sell_levels_with_offset() -> None:
    t = r.build_ticket(config.ASSETS["GOLD"], -1, 2650.0, 10.0, 5.0, r.RiskSettings(10_000, 1, 1.5, 3), True)
    assert t.valid and t.entry == 2655.0 and t.stop_loss == 2670.0 and t.take_profit == 2625.0
    assert t.lots == 0.06 and t.rr_ratio == pytest.approx(2.0)


def test_offset_store_roundtrip(tmp_path) -> None:
    store = r.OffsetStore(tmp_path / "o.json")
    store.calibrate("GOLD", 2660, 2650, "yfinance", "GC=F")
    again = r.OffsetStore(tmp_path / "o.json")
    assert again.get("GOLD", "yfinance", "GC=F").offset == 10
    assert again.get("GOLD", "twelvedata", "XAU/USD") is None  # Otro feed → no se reutiliza
