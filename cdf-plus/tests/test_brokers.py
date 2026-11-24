"""Conector MT5 (con el simulador) y bróker de papel."""
from __future__ import annotations

import pandas as pd
import pytest

import config
from brokers import BrokerError, MT5Broker, PaperBroker, Position, resolve_symbol
from data_fetcher import AssetData, MarketSnapshot
from tests import fake_mt5
from tests.conftest import make_ohlc


@pytest.fixture()
def mt5():
    fake_mt5.reset()
    return fake_mt5


def test_resolve_symbol_aliases(monkeypatch) -> None:
    avail = ["XAUUSD", "US500.cash", "NAS100", "[DJI30]", "US2000m", "XAGUSD", "EURUSD"]
    assert resolve_symbol("GOLD", avail) == "XAUUSD"
    assert resolve_symbol("US500", avail) == "US500.cash"
    assert resolve_symbol("US100", avail) == "NAS100"
    assert resolve_symbol("US30", avail) == "[DJI30]"
    assert resolve_symbol("US2000", avail) == "US2000m"
    monkeypatch.setenv("MT5_SYMBOL_US500", "SPX500")
    assert resolve_symbol("US500", avail) is None  # El override no existe en el bróker


def test_demo_mode_refuses_real_account(mt5) -> None:
    mt5.reset(trade_mode=mt5.ACCOUNT_TRADE_MODE_REAL)
    with pytest.raises(BrokerError, match="REAL"):
        MT5Broker("demo", mt5).connect()


def test_real_mode_requires_confirmation(mt5, monkeypatch) -> None:
    mt5.reset(trade_mode=mt5.ACCOUNT_TRADE_MODE_REAL)
    with pytest.raises(BrokerError, match="BOT_CONFIRM_REAL"):
        MT5Broker("real", mt5).connect()
    monkeypatch.setenv("BOT_CONFIRM_REAL", config.BOT_REAL_CONFIRMATION)
    MT5Broker("real", mt5).connect()


def test_algo_trading_button_required(mt5) -> None:
    mt5.reset(algo=False)
    with pytest.raises(BrokerError, match="Algo Trading"):
        MT5Broker("demo", mt5).connect()


def test_connect_specs_order_and_close(mt5) -> None:
    b = MT5Broker("demo", mt5)
    b.connect()
    assert b.symbols["GOLD"] == "XAUUSD" and b.symbols["US500"] == "US500.cash"
    spec = b.symbol_spec("GOLD")
    assert spec.point_value == pytest.approx(100.0)  # 1.0 USD por tick de 0.01 → 100 por punto
    assert spec.market_open is False                 # Primer tick: aún sin confirmar
    mt5.STATE["tick_time"] += 5
    assert b.symbol_spec("GOLD").market_open is True

    res = b.market_order("GOLD", 1, 0.05, 2620.123, 2710.987)
    assert res.ok
    req = mt5.STATE["requests"][-1]
    assert req["type"] == mt5.ORDER_TYPE_BUY and req["sl"] == 2620.12 and req["magic"] == config.MT5_MAGIC
    assert req["type_filling"] == mt5.ORDER_FILLING_IOC
    pos = b.positions()
    assert len(pos) == 1 and pos[0].key == "GOLD" and pos[0].direction == 1
    assert b.close_position(pos[0]).ok and not b.positions()
    assert b.closed_profit(1) == pytest.approx(-13.0)


def test_rejected_order_check(mt5) -> None:
    b = MT5Broker("demo", mt5)
    b.connect()
    mt5.STATE["reject_check"] = True
    res = b.market_order("GOLD", -1, 0.01, 2700, 2600)
    assert not res.ok and "verificación" in res.message


def test_fetch_snapshot_from_mt5(mt5) -> None:
    b = MT5Broker("demo", mt5)
    b.connect()
    snap = b.fetch_snapshot()
    assert snap.assets["GOLD"].ok and len(snap.assets["GOLD"].frames["4h"]) > 200
    assert b.server_time() is not None


def _snapshot(price: float, bars: pd.DataFrame) -> MarketSnapshot:
    data = AssetData(key="GOLD", provider="test", feed_symbol="GC=F", frames={"15m": bars, "1h": bars, "4h": bars},
                     last_price=price, last_bar_time=bars.index[-1])
    return MarketSnapshot(assets={"GOLD": data}, fetched_at=pd.Timestamp.now(tz="UTC"), provider_requested="test")


def test_paper_broker_tp_and_balance() -> None:
    bars = make_ohlc(50, base=2650, vol=0.0001)
    pb = PaperBroker(10_000)
    pb.update(_snapshot(2650.0, bars))
    res = pb.market_order("GOLD", 1, 0.10, sl=2600.0, tp=2660.0)
    assert res.ok and res.price == pytest.approx(2650.0 + config.TYPICAL_SPREAD["GOLD"] / 2)
    closed = pb.update(_snapshot(2665.0, bars))  # El precio actual supera el TP
    assert closed and closed[0]["reason"] == "TP"
    assert pb.account().balance > 10_000 and not pb.positions()


def test_paper_broker_sl_from_bars() -> None:
    bars = make_ohlc(50, base=2650, vol=0.0001)
    pb = PaperBroker(10_000)
    pb.update(_snapshot(2650.0, bars))
    pb.market_order("GOLD", 1, 0.10, sl=2640.0, tp=2700.0)
    crash = bars.copy()
    new_idx = crash.index[-1] + pd.Timedelta(minutes=15)
    crash.loc[new_idx] = [2650.0, 2651.0, 2630.0, 2645.0, 1.0]  # Mecha que toca el SL
    crash.loc[new_idx + pd.Timedelta(minutes=15)] = [2645.0, 2646.0, 2644.0, 2645.0, 1.0]
    closed = pb.update(_snapshot(2645.0, crash))
    assert closed and closed[0]["reason"] == "SL" and closed[0]["close_price"] == 2640.0


def test_paper_manual_close() -> None:
    bars = make_ohlc(50, base=2650, vol=0.0001)
    pb = PaperBroker(10_000)
    pb.update(_snapshot(2650.0, bars))
    pb.market_order("GOLD", -1, 0.10, sl=2700.0, tp=2600.0)
    p: Position = pb.positions()[0]
    assert pb.close_position(p).ok and not pb.positions()
