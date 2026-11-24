"""Copiloto CFD: puntaje, intermercado, riesgo (lotes y calibración), feed con respaldo, alertas y API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.cfd import analysis as an
from app.cfd.assets import DEFAULT_ASSETS, TF_DELTA, CfdConfig, build_assets
from app.cfd.feed import TWELVEDATA, YAHOO, CfdFeed, FeedError, resample_4h
from app.cfd.risk import TicketInput, build_ticket, lot_size, resolve_offset
from app.cfd.service import CfdService, SettingsPatch, previous_session_close
from app.config import AppConfig
from app.db import AlertRow, Base, make_engine, make_session_factory
from app.main import create_app
from app.providers.base import RateLimited, RateLimiter
from app.providers.fx import FxService
from app.providers.router import ProviderRouter

from .conftest import FakeProvider
from .test_api import FakeFx

NOW = datetime(2026, 11, 24, 15, 0, 30, tzinfo=timezone.utc)  # martes, CME abierto
BASES = {"GC=F": 2650.0, "SI=F": 31.0, "ES=F": 6000.0, "NQ=F": 21000.0, "YM=F": 44000.0, "RTY=F": 2400.0,
         "XAU/USD": 2630.0, "XAG/USD": 30.8}


def intraday(tf: str, n: int = 400, base: float = 100.0, drift: float = 0.0006, seed: int = 3,
             end: datetime = NOW, delay_min: int = 10) -> pd.DataFrame:
    """Velas sintéticas que terminan ~`delay_min` minutos antes de `end` (como un feed con retraso)."""
    step = TF_DELTA[tf]
    last_start = pd.Timestamp(end - timedelta(minutes=delay_min)).floor("min")
    if tf != "1m":
        last_start = last_start.floor({"5m": "5min", "15m": "15min", "1h": "h", "1d": "D"}[tf])
    idx = pd.DatetimeIndex([last_start - step * i for i in range(n)][::-1])
    rng = np.random.default_rng(seed)
    close = base * np.exp(np.cumsum(rng.normal(drift, 0.001, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * 1.0006
    low = np.minimum(open_, close) * 0.9994
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 100.0}, index=idx)


# --- análisis -------------------------------------------------------------------------------------------------

def test_uptrend_scores_high_and_downtrend_low():
    up = an.analyze_tf(intraday("5m", drift=0.002), "5m", pd.Timestamp(NOW))
    down = an.analyze_tf(intraday("5m", drift=-0.002), "5m", pd.Timestamp(NOW))
    # Una tendencia fuerte y pareja lleva el RSI a la zona extrema, que resta a propósito ("no perseguir").
    assert up.score > 40 and up.bias == "alcista"
    assert down.score < -40 and down.bias == "bajista"
    assert {r.id.split(":")[1] for r in up.rules} == {"p_ema200", "ema20_50", "ema50_200", "rsi", "macd", "macd_hist"}
    assert up.last["atr14"] > 0


def test_too_few_bars_gives_no_score():
    a = an.analyze_tf(intraday("1h", n=20), "1h", pd.Timestamp(NOW))
    assert a.score is None and "velas cerradas" in a.reason


def test_without_ema200_trend_rules_are_skipped():
    a = an.analyze_tf(intraday("5m", n=120, drift=0.002), "5m", pd.Timestamp(NOW))
    by = {r.id.split(":")[1]: r for r in a.rules}
    assert not by["p_ema200"].available and not by["ema50_200"].available
    assert a.score is not None and a.score > 0


def test_forming_candle_is_excluded():
    bars = intraday("5m", n=50)
    asof = bars.index[-1] + timedelta(minutes=2)  # la última vela de 5 min todavía se forma
    assert len(an.closed_bars(bars, "5m", asof)) == 49
    assert len(an.closed_bars(bars, "5m", bars.index[-1] + TF_DELTA["5m"])) == 50


def test_resample_4h_follows_cme_session():
    idx = pd.date_range("2026-11-23 23:00", periods=12, freq="h", tz="UTC")  # 17:00 Chicago en adelante
    df = pd.DataFrame({"open": range(12), "high": range(1, 13), "low": range(12), "close": range(12), "volume": 1.0},
                      index=idx, dtype=float)
    out = resample_4h(df)
    hours = [t.tz_convert("America/Chicago").hour for t in out.index]
    assert hours[:3] == [17, 21, 1]
    first = out.iloc[0]
    assert first["open"] == 0 and first["close"] == 3 and first["high"] == 4 and first["volume"] == 4


def test_composite_weights_and_points_add_up():
    asof = pd.Timestamp(NOW)
    by = {tf: an.analyze_tf(intraday(tf, drift=d), tf, asof) for tf, d in (("5m", 0.002), ("15m", 0.002), ("1h", -0.002))}
    score, rules = an.composite(by, "5m")
    expected = 0.5 * by["5m"].score + 0.3 * by["15m"].score + 0.2 * by["1h"].score
    assert score == pytest.approx(expected)
    assert sum(r.points for r in rules) == pytest.approx(score)
    # Sin la temporalidad elegida no hay señal; sin una de contexto, se reparten los pesos.
    assert an.composite({k: v for k, v in by.items() if k != "5m"}, "5m") == (None, [])
    s2, _ = an.composite({"5m": by["5m"], "15m": by["15m"]}, "5m")
    assert s2 == pytest.approx((0.5 * by["5m"].score + 0.3 * by["15m"].score) / 0.8)
    assert an.regime_tf("1m") == "1h" and an.regime_tf("1h") == "4h" and an.regime_tf("4h") == "1d"


def test_regime_and_intermarket_adjustments():
    on = an.regime({"US500": 60, "US100": 50, "US30": 40, "US2000": 50, "GOLD": -10, "SILVER": 0}, "1h")
    assert on.state == "risk-on" and on.breadth == "amplia"
    pts = {r.id: r.points for r in an.intermarket("US500", 40, on)}
    assert pts == {"im:regime": 10, "im:breadth": 5}
    assert {r.id: r.points for r in an.intermarket("GOLD", 40, on)}["im:regime"] == -5

    off = an.regime({"US500": -60, "US100": -50, "US30": -40, "US2000": -50, "GOLD": 40, "SILVER": 30}, "1h")
    assert off.state == "risk-off"
    gold = {r.id: r.points for r in an.intermarket("GOLD", 50, off)}
    assert gold == {"im:regime": 10, "im:metal": 5}  # refugio + la plata confirma

    narrow = an.regime({"US500": 50, "US100": 50, "US30": 40, "US2000": -30, "GOLD": 0, "SILVER": 0}, "1h")
    assert narrow.breadth == "débil"
    assert {r.id: r.points for r in an.intermarket("US100", 40, narrow)}["im:breadth"] == -5
    assert an.intermarket("US500", 0, on) == []  # sin dirección no se ajusta


def test_verdicts():
    assert [an.verdict(v) for v in (85, 70, 45, 10, -31, -70, None)] == [
        "COMPRA FUERTE", "COMPRA FUERTE", "COMPRA", "NEUTRAL", "VENTA", "VENTA FUERTE", "SIN DATOS"]
    assert an.clip(130) == 100 and an.clip(-130) == -100


# --- riesgo -----------------------------------------------------------------------------------------------------

def test_lot_size_rounds_down_and_respects_min_lot():
    # 10 USD de riesgo, stop de 4 puntos en US500 (50 USD/punto/lote): 10 / 200 = 0,05 lotes exactos.
    assert lot_size(10, 4, 50) == (0.05, pytest.approx(10.0))
    lots, risk = lot_size(10, 3.1, 50)  # 0,0645… → 0,06 (nunca más riesgo del pedido)
    assert lots == 0.06 and risk <= 10
    assert lot_size(10, 30, 50) == (0.0, 0.0)  # ni 0,01 lotes cabe (15 USD)
    lots_sp, risk_sp = lot_size(100, 4, 50, spread_points=1)  # el spread se suma al riesgo
    assert lots_sp == 0.4 and risk_sp == pytest.approx(100)
    for bad in ((10, 0, 50), (10, 4, 0), (0, 4, 50), (10, float("nan"), 50)):
        with pytest.raises(ValueError):
            lot_size(*bad)


def test_ticket_long_short_and_offset():
    a = DEFAULT_ASSETS["US500"]
    t = build_ticket(TicketInput(1, 6000.0, 4.0, 12.5, 10_000, 1.0, 1.5, 3.0), a)
    assert t["ok"] and t["direction"] == "LARGO" and t["calibrated"]
    assert t["entry"] == 6012.5 and t["sl"] == 6006.5 and t["tp"] == 6024.5
    assert t["lots"] == 0.33 and t["risk_usd"] <= 100 and t["rr"] == 2.0
    s = build_ticket(TicketInput(-1, 6000.0, 4.0, None, 10_000, 1.0, 1.5, 3.0), a)
    assert s["sl"] > s["entry"] > s["tp"] and not s["calibrated"]
    assert any("Sin calibrar" in w for w in s["warnings"])


def test_ticket_guards():
    a = DEFAULT_ASSETS["GOLD"]
    assert "ATR" in build_ticket(TicketInput(1, 2650, None, 0, 1000, 1, 1.5, 3), a)["error"]
    assert "ATR" in build_ticket(TicketInput(1, 2650, 0.0, 0, 1000, 1, 1.5, 3), a)["error"]
    assert "capital" in build_ticket(TicketInput(1, 2650, 5, 0, 0, 1, 1.5, 3), a)["error"]
    assert "neutral" in build_ticket(TicketInput(0, 2650, 5, 0, 1000, 1, 1.5, 3), a)["error"]
    small = build_ticket(TicketInput(1, 2650, 8, 0, 500, 1, 1.5, 3), a)  # 0,01 lote arriesga 12 USD > 5 USD
    assert not small["ok"] and small["lots"] == 0 and "lote mínimo" in small["error"]
    assert small["min_lot_risk_usd"] == pytest.approx(12.0)


def test_resolve_offset_waits_for_delayed_feed():
    bars = intraday("1m", n=60, base=2650)  # el feed llega hasta NOW − 10 min
    at_recent = NOW - timedelta(minutes=2)
    assert resolve_offset(2660.0, at_recent, bars) is None  # pendiente
    at = bars.index[-5] + timedelta(seconds=20)
    res = resolve_offset(2660.0, at, bars)
    assert res["feed_price"] == pytest.approx(bars["close"].iloc[-5])
    assert res["offset"] == pytest.approx(2660.0 - bars["close"].iloc[-5])
    gap = bars.drop(bars.index[-40:-5])
    with pytest.raises(ValueError, match="cerca de esa hora"):
        resolve_offset(2660.0, bars.index[-10], gap)


def test_previous_session_close():
    idx = pd.date_range("2026-11-22 23:00", "2026-11-24 14:00", freq="h", tz="UTC")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": np.arange(len(idx), dtype=float) + 1,
                       "volume": 1.0}, index=idx)
    # Sesión actual: desde el lunes 23-nov 17:00 Chicago (23:00 UTC); el cierre anterior es la vela de las 22:00 UTC.
    prev = previous_session_close(df, NOW)
    assert prev == df.loc[pd.Timestamp("2026-11-23 22:00", tz="UTC"), "close"]


# --- feed ---------------------------------------------------------------------------------------------------------

class FakeTD:
    api_key = "k"

    def is_configured(self):
        return True


def test_feed_falls_back_to_yahoo_and_caches():
    calls = {"td": 0, "yahoo": 0}

    def td(provider, sym, tf):
        calls["td"] += 1
        raise RateLimited("sin cupo")

    def yahoo(sym, tf):
        calls["yahoo"] += 1
        return intraday(tf, base=BASES[sym])

    clock = [0.0]
    feed = CfdFeed(build_assets(CfdConfig()), FakeTD(), True, td_fetch=td, yahoo_fetch=yahoo, clock=lambda: clock[0])
    fb = feed.bars("GOLD", "1m")
    assert fb.source == YAHOO and fb.notes and "sin cupo" in fb.notes[0]
    assert feed.sources_for("GOLD", "1m") == [TWELVEDATA, YAHOO]
    assert feed.sources_for("GOLD", "1h") == [YAHOO] and feed.sources_for("US500", "1m") == [YAHOO]
    feed.bars("US500", "5m")
    feed.bars("US500", "5m")
    assert calls["yahoo"] == 2  # la segunda vino de la caché
    clock[0] = 500
    feed.bars("US500", "5m")
    assert calls["yahoo"] == 3


def test_feed_keeps_last_bars_when_source_fails():
    state = {"fail": False}

    def yahoo(sym, tf):
        if state["fail"]:
            raise ConnectionError("sin red")
        return intraday(tf, base=BASES[sym])

    clock = [0.0]
    feed = CfdFeed(build_assets(CfdConfig()), None, yahoo_fetch=yahoo, clock=lambda: clock[0])
    feed.bars("US30", "1m")
    state["fail"], clock[0] = True, 100
    fb = feed.bars("US30", "1m")
    assert fb.error and "sin red" in fb.error and not fb.bars.empty
    with pytest.raises(FeedError):
        feed.bars("US2000", "1m")


def test_feed_limiter_is_respected():
    lim = RateLimiter(per_minute=1)

    def yahoo(sym, tf):
        return intraday(tf, base=BASES[sym])

    feed = CfdFeed(build_assets(CfdConfig()), None, yahoo_limiter=lim, yahoo_fetch=yahoo)
    feed.bars("US30", "1m")
    with pytest.raises(FeedError, match="límite propio"):
        feed.bars("US30", "5m")


# --- servicio y alertas -----------------------------------------------------------------------------------------

class FakeNotifier:
    def __init__(self):
        self.sent = []

    def configured(self):
        return {"telegram": True, "email": False}

    def send(self, title, message):
        self.sent.append((title, message))
        return ["telegram"], []


def make_service(drift=0.002, open_=True, now=NOW):
    eng = make_engine(":memory:")
    Base.metadata.create_all(eng)
    S = make_session_factory(eng)

    def yahoo(sym, tf):
        # Índices al alza (el Russell 2000 más suave), metales a la baja: clima risk-on.
        d = -drift if sym in ("GC=F", "SI=F") else drift / 2 if sym == "RTY=F" else drift
        return intraday(tf, base=BASES[sym], drift=d, end=now[0] if isinstance(now, list) else now)

    feed = CfdFeed(build_assets(CfdConfig()), None, yahoo_fetch=yahoo, clock=lambda: 0.0)
    n = FakeNotifier()
    clock = now if isinstance(now, list) else [now]
    svc = CfdService(S, feed, n, now=lambda: clock[0], open_fn=lambda t: open_)
    return svc, n, S, feed


def test_overview_rows_and_regime():
    svc, *_ = make_service()
    ov = svc.overview("5m")
    assert ov["tf"] == "5m" and ov["market_open"] and len(ov["assets"]) == 6
    assert ov["regime"]["state"] == "risk-on"
    us100 = next(r for r in ov["assets"] if r["code"] == "US100")
    assert us100["score"] > 30 and us100["verdict"].startswith("COMPRA")
    assert any(r["id"] == "im:regime" and r["points"] == 10 for r in svc.detail("US100", "5m")["rules"])
    gold = next(r for r in ov["assets"] if r["code"] == "GOLD")
    assert gold["score"] < -30 and gold["verdict"].startswith("VENTA")
    assert gold["delay_min"] == pytest.approx(9.5, abs=0.6)
    assert set(gold["bias"]) == {"1m", "5m", "15m", "1h", "4h", "1d"}
    assert gold["xtb_price"] is None and gold["calibration"]["state"] == "sin calibrar"
    assert gold["ticket"]["direction"] == "CORTO" and gold["ticket"]["sl"] > gold["ticket"]["entry"]
    with pytest.raises(ValueError):
        svc.overview("2m")


def test_calibration_pending_then_resolved():
    clock = [NOW]
    svc, *_ = make_service(now=clock)
    v = svc.calibrate("US500", 6050.0)
    assert v["state"] == "pendiente" and v["offset"] is None
    clock[0] = NOW + timedelta(minutes=12)  # el feed ya trae la vela de esa hora
    svc.feed._cache.clear()
    row = svc.overview("5m")["assets"][2]
    assert row["code"] == "US500" and row["calibration"]["state"] == "ok"
    assert row["xtb_price"] == pytest.approx(row["feed_price"] + row["offset"], abs=0.06)
    assert row["ticket"]["calibrated"]
    svc.clear_calibration("US500")
    assert svc.calibration("US500") is None
    manual = svc.calibrate("GOLD", manual_offset=-18.4)
    assert manual["state"] == "ok" and manual["offset"] == -18.4
    with pytest.raises(ValueError):
        svc.calibrate("GOLD", 0)


def test_settings_validation_and_asset_overrides():
    svc, *_ = make_service()
    st = svc.save_settings(SettingsPatch(capital_usd=5000, risk_pct=0.5, timeframe="1m",
                                         assets={"US30": {"spread_points": 2.0}}))
    assert st.capital_usd == 5000 and st.timeframe == "1m"
    assert svc.assets()["US30"].spread_points == 2.0 and svc.assets()["US30"].value_per_point == 5.0
    with pytest.raises(Exception):
        SettingsPatch(risk_pct=50)


def test_alerts_once_per_candle_and_only_on_entry():
    svc, n, S, _ = make_service(drift=0.004)
    svc.save_settings(SettingsPatch(alerts_enabled=True, alert_threshold=60))
    first = svc.scan()
    assert first and all("CFD" in a["title"] for a in first)
    assert n.sent and "entrada" in n.sent[0][1] and "no opera" in n.sent[0][1]
    assert svc.scan() == []  # misma vela / sigue fuerte: no se repite
    svc._strong.clear()      # p. ej. tras reiniciar: la clave por vela evita el duplicado
    assert svc.scan() == []
    with S() as s:
        rows = s.query(AlertRow).filter(AlertRow.kind == "cfd").all()
    assert len(rows) == len(first) and all(r.notified == "telegram" for r in rows)


def test_no_alerts_when_disabled_or_market_closed():
    svc, n, *_ = make_service(drift=0.004)
    assert svc.scan() == []  # desactivadas por defecto
    closed, n2, *_ = make_service(drift=0.004, open_=False)
    closed.save_settings(SettingsPatch(alerts_enabled=True, alert_threshold=60))
    assert closed.scan() == [] and not n2.sent


# --- API -----------------------------------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    cfg = AppConfig.model_validate({"watchlist": ["SPY"], "general": {"database": str(tmp_path / "t.db")}})
    order = {"US": ["yfinance"], "CL": ["yfinance"], "FX": ["yfinance"]}
    router = ProviderRouter({"yfinance": FakeProvider("yfinance")}, order, order)

    def yahoo(sym, tf):
        return intraday(tf, base=BASES[sym], end=datetime.now(timezone.utc))

    feed = CfdFeed(build_assets(cfg.cfd), None, yahoo_fetch=yahoo)
    app = create_app(cfg, router, FxService([FakeFx()]), db_path=str(tmp_path / "t.db"), start_poller=False,
                     notifier=FakeNotifier(), cfd_feed=feed)
    with TestClient(app) as c:
        yield c


def test_api_endpoints(client):
    ov = client.get("/api/cfd/overview?tf=15m").json()
    assert ov["tf"] == "15m" and len(ov["assets"]) == 6 and "no ejecuta" in ov["disclaimer"]
    assert ov["contracts"]["GOLD"]["value_per_point"] == 100 and ov["alerts"]["telegram"] is True
    d = client.get("/api/cfd/assets/us100?tf=1m").json()
    assert d["code"] == "US100" and len(d["chart"]["bars"]) == 300 and d["rules"]
    assert {"t", "open", "ema20", "ema200"} <= set(d["chart"]["bars"][-1])
    assert client.get("/api/cfd/assets/XXX").status_code == 404
    assert client.get("/api/cfd/overview?tf=3m").status_code == 422
    st = client.put("/api/cfd/settings", json={"capital_usd": 2500, "sl_atr": 2}).json()
    assert st["capital_usd"] == 2500 and st["sl_atr"] == 2
    assert client.put("/api/cfd/settings", json={"risk_pct": 0}).status_code == 422
    cal = client.post("/api/cfd/calibrate", json={"asset": "GOLD", "manual_offset": 5.5}).json()
    assert cal["offset"] == 5.5
    assert client.post("/api/cfd/calibrate", json={"asset": "GOLD", "xtb_price": -1}).status_code == 422
    assert client.delete("/api/cfd/calibrate/GOLD").json() == {"cleared": "GOLD"}
    assert client.post("/api/cfd/scan").json()["alerts_enabled"] is False
