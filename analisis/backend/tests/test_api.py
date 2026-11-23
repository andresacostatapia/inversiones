import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.main import create_app
from app.providers.base import ProviderError
from app.providers.fx import FxRate, FxService
from app.providers.router import ProviderRouter

from .conftest import FakeProvider


class FakeFx:
    name = "fake"

    def latest(self):
        from datetime import datetime, timezone

        return FxRate(pair="USDCLP", rate=950.0, as_of=datetime(2026, 11, 23, 12, tzinfo=timezone.utc),
                      source="fake", kind="observado", prev=955.0)


@pytest.fixture
def setup(tmp_path):
    cfg = AppConfig.model_validate({"watchlist": ["SPY", "SQM-B.SN"], "general": {"database": str(tmp_path / "t.db")}})
    prov = FakeProvider("yfinance")
    order = {"US": ["yfinance"], "CL": ["yfinance"], "FX": ["yfinance"]}
    router = ProviderRouter({"yfinance": prov}, order, order)
    app = create_app(cfg, router, FxService([FakeFx()]), db_path=str(tmp_path / "t.db"), start_poller=False)
    with TestClient(app) as client:
        yield client, prov


def test_status_has_disclaimer_and_markets(setup):
    client, _ = setup
    st = client.get("/api/status").json()
    assert st["disclaimer"] == "Herramienta educativa, no es asesoría financiera."
    assert {m["market"] for m in st["markets"]} == {"CL", "US", "FX"}
    assert st["providers"][0]["name"] == "yfinance"


def test_watchlist_seeded_from_config_then_refresh(setup):
    client, _ = setup
    wl = client.get("/api/watchlist").json()
    assert [w["symbol"] for w in wl] == ["SPY", "SQM-B.SN"]
    assert wl[0]["status"] == "sin_datos" and wl[0]["freshness"]["kind"] == "sin_datos"

    res = client.post("/api/refresh").json()
    assert res["quotes"] == {"SPY": True, "SQM-B.SN": True} and res["fx"] is True
    assert set(res["history"]) == {"SPY", "SQM-B.SN"}

    spy = client.get("/api/quotes/SPY").json()
    assert spy["price"] > 0 and spy["source"] == "yfinance" and spy["name"] == "Nombre SPY"
    assert spy["change_pct"] == pytest.approx(1.0)
    assert spy["freshness"]["label"]  # siempre se informa la hora y el tipo de dato
    fx = client.get("/api/fx").json()
    assert fx["price"] == 950.0 and "observado" in fx["source"]


def test_add_and_remove_symbol(setup):
    client, prov = setup
    r = client.post("/api/watchlist", json={"symbol": " voo "})
    assert r.status_code == 201 and r.json()["symbol"] == "VOO"
    assert ("history", "VOO") in prov.calls
    assert [w["symbol"] for w in client.get("/api/watchlist").json()][-1] == "VOO"

    assert client.post("/api/watchlist", json={"symbol": "NOEXISTE.SN"}).status_code == 404
    assert client.post("/api/watchlist", json={"symbol": "mal símbolo!"}).status_code == 422

    assert client.delete("/api/watchlist/voo").status_code == 200
    assert client.delete("/api/watchlist/VOO").status_code == 404


def test_provider_down_when_adding_gives_502(setup):
    client, prov = setup
    prov.fail_with = ProviderError("sin red")
    r = client.post("/api/watchlist", json={"symbol": "VOO"})
    assert r.status_code == 502 and "no respondieron" in r.json()["detail"]


def test_failed_refresh_keeps_last_good_price(setup):
    client, prov = setup
    client.post("/api/refresh")
    before = client.get("/api/quotes/SPY").json()
    prov.fail_with = ProviderError("Yahoo caído")
    client.post("/api/refresh")
    after = client.get("/api/quotes/SPY").json()
    assert after["price"] == before["price"]
    assert after["status"] == "error" and "Yahoo caído" in after["error"]


def test_history_and_indicators(setup):
    client, _ = setup
    assert client.get("/api/instruments/SPY/history").status_code == 404  # aún sin historia
    client.post("/api/refresh")
    h = client.get("/api/instruments/SPY/history", params={"period": "3m"}).json()
    assert 55 <= len(h["bars"]) <= 70
    first = h["bars"][0]
    assert first["sma200"] is not None  # calculado sobre toda la historia antes de recortar
    assert {"rsi14", "macd", "bb_upper", "atr14", "rel_volume"} <= set(first)

    snap = client.get("/api/instruments/SPY/indicators").json()
    assert 0 <= snap["indicators"]["rsi14"] <= 100
    assert snap["metrics"]["return_1m_pct"] is not None
    assert snap["quote"]["symbol"] == "SPY" and isinstance(snap["provisional"], bool)
    assert client.get("/api/instruments/SPY/history", params={"period": "xx"}).status_code == 422


def test_today_bar_follows_live_price(setup):
    client, prov = setup
    client.post("/api/refresh")
    prov.price["SPY"] = 999.0
    client.post("/api/refresh")
    bars = client.get("/api/instruments/SPY/history", params={"period": "1m"}).json()["bars"]
    last = bars[-1]
    assert last["date"] == "2026-11-23" and last["close"] == 999.0 and last["high"] >= 999.0


# --- Fase 2 -------------------------------------------------------------------

def test_signals_endpoints(setup):
    client, _ = setup
    assert client.get("/api/signals/SPY").status_code == 404  # sin historia todavía
    res = client.post("/api/refresh").json()
    assert set(res["signals"]) == {"SPY", "SQM-B.SN"}  # el ciclo recalcula y guarda las señales

    data = client.get("/api/signals").json()
    assert data["disclaimer"].startswith("Herramienta educativa")
    spy = next(s for s in data["signals"] if s["symbol"] == "SPY")
    assert spy["label"] in ("COMPRAR", "MANTENER", "VENDER")
    assert -100 <= spy["score"] <= 100 and 0 <= spy["confidence"] <= 100
    assert spy["summary"] and spy["reasons"] and len(spy["strategies"]) == 4
    assert spy["price"]["freshness"]["label"]

    one = client.get("/api/signals/spy").json()
    assert one["symbol"] == "SPY" and one["previous"] is None and one["changed"] is False

    hist = client.get("/api/signals/SPY/history", params={"period": "3m"}).json()
    assert 55 <= len(hist["points"]) <= 70 and "composite" in hist["points"][-1]

    strat = client.get("/api/strategies").json()
    assert [s["key"] for s in strat["strategies"]] == ["trend", "rsi", "macd", "bollinger"]


def test_signal_change_is_detected(setup):
    client, prov = setup
    client.post("/api/refresh")
    app = client.app
    from datetime import date

    from app.db import SignalRow

    with app.state.md.Session() as s:  # simula que ayer la señal era otra
        s.add(SignalRow(symbol="SPY", date=date(2026, 11, 20), computed_at=prov.as_of, label="NADA", score=0,
                        confidence=0, provisional=False, summary="", payload="{}"))
        s.commit()
    one = client.get("/api/signals/SPY").json()
    assert one["previous"]["label"] == "NADA" and one["changed"] is True


def test_backtest_endpoint(setup):
    client, _ = setup
    assert client.post("/api/backtest", json={"symbol": "SPY"}).status_code == 404
    client.post("/api/refresh")
    r = client.post("/api/backtest", json={"symbol": "SPY", "years": 0.2, "commission_pct": 0.1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assumptions"]["commission_pct"] == 0.1
    assert body["strategies"][-1]["key"] == "composite"
    assert body["buy_hold"]["metrics"]["total_return_pct"] is not None

    r = client.post("/api/backtest", json={"symbol": "SPY", "strategies": ["rsi"], "buy_threshold": 20})
    assert [s["key"] for s in r.json()["strategies"]] == ["rsi", "composite"]
    assert r.json()["assumptions"]["thresholds"]["buy"] == 20

    assert client.post("/api/backtest", json={"symbol": "SPY", "strategies": ["magia"]}).status_code == 422
    assert client.post("/api/backtest", json={"symbol": "SPY", "buy_threshold": -40}).status_code == 422
