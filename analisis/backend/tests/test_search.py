"""Búsqueda de símbolos, mercados 24/5 y 24/7, otras bolsas y qué permite comprar el perfil."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.main import create_app
from app.market_hours import freshness, is_open, session_info
from app.portfolio import Candidate, investability, suggest_contribution
from app.providers.base import market_of
from app.providers.fx import FxService
from app.providers.router import ProviderRouter

from .conftest import FakeProvider
from .test_api import FakeFx
from .test_phase4 import FakeNotifier

UTC = timezone.utc


@pytest.mark.parametrize("sym,market", [
    ("SQM-B.SN", "CL"), ("SPY", "US"), ("BRK-B", "US"), ("^GSPC", "US"), ("CLP=X", "FX"), ("USDCLP", "FX"),
    ("GC=F", "GLB"), ("EURUSD=X", "GLB"), ("BTC-USD", "CRY"), ("005930.KS", "OTHER"), ("SMSN.IL", "OTHER"),
])
def test_market_of(sym, market):
    assert market_of(sym) == market


def test_globex_crypto_and_other_hours():
    wed = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)
    assert is_open("GLB", wed)
    assert is_open("GLB", datetime(2026, 11, 25, 3, 0, tzinfo=UTC))       # de madrugada también
    assert not is_open("GLB", datetime(2026, 11, 25, 22, 30, tzinfo=UTC))  # pausa diaria 16:00–17:00 Chicago
    assert not is_open("GLB", datetime(2026, 11, 28, 15, 0, tzinfo=UTC))   # sábado
    assert not is_open("GLB", datetime(2026, 11, 29, 20, 0, tzinfo=UTC))   # domingo antes de las 17:00 Chicago
    assert is_open("GLB", datetime(2026, 11, 29, 23, 30, tzinfo=UTC))      # domingo después
    assert is_open("CRY", datetime(2026, 11, 28, 15, 0, tzinfo=UTC))       # cripto: siempre
    assert not is_open("OTHER", wed)
    assert session_info("GLB", wed) == {"market": "GLB", "name": "Futuros y divisas", "open": True}


def test_freshness_unknown_hours():
    now = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)
    f = freshness(now - timedelta(minutes=5), realtime=False, market_open=False, now=now, hours_known=False)
    assert f["kind"] == "cierre" and f["label"] == "Último precio 11:55 · horario de esa bolsa no disponible"


@pytest.mark.parametrize("sym,kind,cur,name,blocked", [
    ("NVDA", "EQUITY", "USD", "NVIDIA Corporation", None),
    ("GLD", "ETF", "USD", "SPDR Gold Shares", None),
    ("SQM-B.SN", "EQUITY", "CLP", "SQM", None),
    ("BTC-USD", "CRYPTOCURRENCY", "USD", "Bitcoin USD", None),
    ("SHV", "ETF", "USD", "iShares Short Treasury Bond ETF", None),   # "Short" de corto plazo: no es inverso
    ("^GSPC", "INDEX", "USD", "S&P 500", "índice"),
    ("GC=F", "FUTURE", "USD", "Gold Dec 26", "derivado"),
    ("EURUSD=X", "CURRENCY", "USD", "EUR/USD", "tipo de cambio"),
    ("005930.KS", "EQUITY", "KRW", "Samsung Electronics", "KRW"),
    ("TQQQ", "ETF", "USD", "ProShares UltraPro QQQ", "apalancado"),
    ("SOXL", "ETF", "USD", "Direxion Daily Semiconductor Bull 3X Shares", "apalancado"),
    ("NVDX", "ETF", "USD", "T-Rex 2X Long NVIDIA Daily Target ETF", "apalancado"),
    ("SH", "ETF", "USD", "ProShares Short S&P500", "apalancado o inverso"),
])
def test_investability(sym, kind, cur, name, blocked):
    r = investability(sym, kind, cur, name)
    if blocked is None:
        assert r is None
    else:
        assert r is not None and blocked in r


def test_investability_respects_profile_flags():
    assert investability("GC=F", "FUTURE", "USD", "Gold", allow_derivatives=True) is None
    assert investability("TQQQ", "ETF", "USD", "ProShares UltraPro QQQ", allow_leverage=True, allow_short=True) is None


def test_contribution_skips_blocked_candidates():
    cs = [Candidate("SPY", 700, "USD", 700_000, "COMPRAR"),
          Candidate("^GSPC", 6000, "USD", 6_000_000, "COMPRAR", blocked="Es un índice")]
    r = suggest_contribution(2_000_000, {"CL": 0, "INTL": 0}, cs, {"CL": 0.0, "INTL": 1.0}, {"US": False})
    assert [line.symbol for line in r.lines] == ["SPY"]
    assert r.excluded == [{"symbol": "^GSPC", "signal": "COMPRAR", "reason": "Es un índice"}]


# --- API ------------------------------------------------------------------------------

class KindProvider(FakeProvider):
    KINDS = {"^GSPC": "INDEX", "GC=F": "FUTURE", "SPY": "ETF"}

    def get_quote(self, symbol):
        q = super().get_quote(symbol)
        q.kind = self.KINDS.get(symbol, "EQUITY")
        return q


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = AppConfig.model_validate({"watchlist": ["SPY"], "general": {"database": str(tmp_path / "t.db")}})
    prov = KindProvider("yfinance", markets=("US", "CL", "FX", "GLB"), known=("SPY", "^GSPC", "GC=F", "CLP=X"))
    order = {"US": ["yfinance"], "CL": ["yfinance"], "FX": ["yfinance"], "GLB": ["yfinance"]}
    app = create_app(cfg, ProviderRouter({"yfinance": prov}, order, order), FxService([FakeFx()]),
                     db_path=str(tmp_path / "t.db"), start_poller=False, notifier=FakeNotifier())
    import app.api.routes as routes

    monkeypatch.setattr(routes, "yahoo_search", lambda q: [
        {"symbol": "^GSPC", "name": "S&P 500", "exchange": "SNP", "kind": "INDEX", "kind_label": "Índice"},
        {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "exchange": "NYSEArca", "kind": "ETF", "kind_label": "ETF"},
        {"symbol": "ES=F", "name": "E-Mini S&P 500", "exchange": "CME", "kind": "FUTURE", "kind_label": "Futuro"},
    ])
    with TestClient(app) as c:
        yield c


def test_search_endpoint(client):
    r = client.get("/api/search", params={"q": "s&p 500"}).json()
    by = {x["symbol"]: x for x in r["results"]}
    assert by["SPY"]["note"] is None and by["SPY"]["in_watchlist"] is True and by["SPY"]["market"] == "US"
    assert "índice" in by["^GSPC"]["note"]
    assert by["ES=F"]["market"] == "GLB" and "derivado" in by["ES=F"]["note"]
    assert client.get("/api/search", params={"q": ""}).status_code == 422


def test_index_and_future_can_be_watched_but_not_bought(client):
    client.post("/api/refresh")
    assert client.post("/api/watchlist", json={"symbol": "^gspc"}).status_code == 201
    assert client.post("/api/watchlist", json={"symbol": "GC=F"}).status_code == 201
    q = client.get("/api/quotes/%5EGSPC").json()
    assert q["kind"] == "INDEX" and q["price"] > 0
    assert client.get("/api/signals/%5EGSPC").status_code == 200  # se puede analizar
    body = {"side": "compra", "quantity": 1, "price": 100, "date": "2026-06-01", "fx_rate": 950}
    r = client.post("/api/transactions", json={**body, "symbol": "^GSPC"})
    assert r.status_code == 422 and "ETF" in r.json()["detail"]
    r = client.post("/api/transactions", json={**body, "symbol": "GC=F"})
    assert r.status_code == 422 and "derivado" in r.json()["detail"]
    assert client.post("/api/transactions", json={**body, "symbol": "SPY"}).status_code == 201
