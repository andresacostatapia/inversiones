"""Cartera, aportes, importación y alertas a través de la API (sin red)."""

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.alerts import Notifier
from app.config import AppConfig, Secrets
from app.db import LatestQuote, SignalRow
from app.main import create_app
from app.providers.base import ProviderError
from app.providers.fx import FxService
from app.providers.router import ProviderRouter

from .conftest import FakeProvider
from .test_api import FakeFx


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def configured(self):
        return {"telegram": True, "email": False}

    def send(self, title, message):
        self.sent.append(title)
        return ["telegram"], []


@pytest.fixture
def env(tmp_path):
    cfg = AppConfig.model_validate({
        "watchlist": ["SPY", "SQM-B.SN"],
        "general": {"database": str(tmp_path / "t.db")},
        "risk_profile": {"monthly_contribution_clp": 1_000_000},
    })
    prov = FakeProvider("yfinance")
    order = {"US": ["yfinance"], "CL": ["yfinance"], "FX": ["yfinance"]}
    notifier = FakeNotifier()
    app = create_app(cfg, ProviderRouter({"yfinance": prov}, order, order), FxService([FakeFx()]),
                     db_path=str(tmp_path / "t.db"), start_poller=False, notifier=notifier)
    with TestClient(app) as client:
        yield client, prov, notifier


def buy(client, symbol="SPY", qty=10, price=100.0, d="2026-06-01", **kw):
    return client.post("/api/transactions", json={"symbol": symbol, "side": "compra", "quantity": qty,
                                                  "price": price, "date": d, **kw})


def test_transactions_crud_and_validation(env):
    client, _, _ = env
    # Sin historia del dólar ni dólar actual: hay que ingresarlo a mano.
    r = buy(client)
    assert r.status_code == 422 and "dólar" in r.json()["detail"]
    r = buy(client, fx_rate=900)
    assert r.status_code == 201 and r.json()["fx_source"] == "manual" and r.json()["currency"] == "USD"

    client.post("/api/refresh")  # trae el dólar y su historia
    r = buy(client, d="2026-07-01")
    assert r.status_code == 201 and r.json()["fx_source"] == "historico" and 800 < r.json()["fx_rate"] < 1200

    # Símbolo fuera de la watchlist: se valida y se agrega.
    assert buy(client, symbol="VOO", qty=1).status_code == 201
    assert "VOO" in [w["symbol"] for w in client.get("/api/watchlist").json()]

    assert buy(client, symbol="NOEXISTE").status_code == 404
    assert buy(client, d="2999-01-01").status_code == 422
    sell = {"symbol": "SPY", "side": "venta", "quantity": 25, "price": 110, "date": "2026-08-01"}
    r = client.post("/api/transactions", json=sell)
    assert r.status_code == 422 and "cortos" in r.json()["detail"]
    sell["quantity"] = 15
    sell_id = client.post("/api/transactions", json=sell).json()["id"]

    txs = client.get("/api/transactions").json()
    assert len(txs) == 4 and txs[0]["side"] == "venta"
    # Borrar una compra dejaría la venta sin respaldo.
    first_buy = next(t for t in txs if t["date"] == "2026-06-01" and t["symbol"] == "SPY")
    r = client.delete(f"/api/transactions/{first_buy['id']}")
    assert r.status_code == 422 and "venta posterior" in r.json()["detail"]
    assert client.delete(f"/api/transactions/{sell_id}").status_code == 200
    assert client.delete(f"/api/transactions/{first_buy['id']}").status_code == 200
    assert client.delete("/api/transactions/9999").status_code == 404


def test_portfolio_and_contribution(env):
    client, prov, _ = env
    client.post("/api/refresh")
    buy(client, "SPY", 10, 90)              # USD
    buy(client, "SQM-B.SN", 1000, 100)      # CLP
    pf = client.get("/api/portfolio").json()
    assert pf["disclaimer"].startswith("Herramienta educativa")
    spy = next(p for p in pf["positions"] if p["symbol"] == "SPY")
    assert spy["price"] == prov.price["SPY"] and spy["value_clp"] == pytest.approx(10 * prov.price["SPY"] * 950)
    assert spy["freshness"]["label"]
    t = pf["totals"]
    assert t["value_usd"] == pytest.approx(t["value_clp"] / 950)
    assert {a["bucket"] for a in pf["allocation"]} == {"CL", "INTL"}

    c = client.post("/api/contribution", json={}).json()  # usa el aporte del perfil (1.000.000)
    assert c["amount_clp"] == 1_000_000
    assert sum(line["amount_clp"] for line in c["lines"]) + c["unallocated_clp"] == pytest.approx(1_000_000, abs=2)
    assert client.post("/api/contribution", json={"amount_clp": -5}).status_code == 422
    assert client.get("/api/profile").json()["target_allocation"] == {"CL": 0.3, "INTL": 0.7}


PWA_CSV = (
    "﻿Fecha;Activo;Tipo;Cantidad;Precio;Comisión;Impuestos;Moneda;Tipo de cambio;Broker;G/P realizada CLP;"
    "G/P realizada USD;Nota\r\n"
    "2026-03-02;SPY;compra;2;650.5;1;0;USD;940.2;Racional;;;primera\r\n"
    "2026-03-05;SQM-B;compra;10;48.500;0;0;CLP;945;Zesty;;;\r\n"
    "05/04/2026;SPY;venta;1;700;1;0;USD;960;Racional;;;\r\n"
    "2026-04-06;SPY;regalo;1;700;0;0;USD;960;Racional;;;\r\n"
)


def test_import_pwa_csv(env):
    client, _, _ = env
    prev = client.post("/api/transactions/import", json={"csv": PWA_CSV}).json()
    assert prev["ready"] == 3 and prev["errors"] == 1 and prev["imported"] == 0
    rows = prev["rows"]
    assert rows[1]["symbol"] == "SQM-B.SN" and rows[1]["price"] == 48500  # miles chilenos y sufijo .SN
    assert rows[2]["date"] == "2026-04-05" and rows[0]["fee"] == 1 and rows[0]["fx_rate"] == 940.2
    assert client.get("/api/transactions").json() == []  # la vista previa no guarda

    done = client.post("/api/transactions/import", json={"csv": PWA_CSV, "commit": True}).json()
    assert done["imported"] == 3
    again = client.post("/api/transactions/import", json={"csv": PWA_CSV}).json()
    assert again["duplicates"] == 3 and again["ready"] == 0

    bad = "Fecha;Activo;Tipo;Cantidad;Precio\n2026-01-01;VOO;venta;5;100\n"
    r = client.post("/api/transactions/import", json={"csv": bad}).json()
    assert "cortos" in r["consistency_error"]
    assert client.post("/api/transactions/import", json={"csv": bad, "commit": True}).status_code == 422
    assert client.post("/api/transactions/import", json={"csv": "a;b\n1;2"}).status_code == 422


def test_drawdown_alerts_lifecycle(env):
    client, prov, notifier = env
    client.post("/api/refresh")
    p = prov.price["SPY"]
    buy(client, "SPY", 10, p / 0.7)  # el precio actual está 30 % bajo el costo
    res = client.post("/api/refresh").json()
    assert any("VENTA" in a for a in res["alerts"])
    data = client.get("/api/alerts").json()
    venta = [a for a in data["alerts"] if a["kind"] == "venta"]
    assert len(venta) == 1 and venta[0]["severity"] == "critical" and venta[0]["notified"] == ["telegram"]
    assert data["unread"] >= 1 and data["channels"]["telegram"] is True

    client.post("/api/refresh")  # la condición sigue: no se repite
    assert len([a for a in client.get("/api/alerts").json()["alerts"] if a["kind"] == "venta"]) == 1

    prov.price["SPY"] = p / 0.7 * 0.78  # se recupera a -22 %: se resuelve la de venta y aparece la de revisión
    client.post("/api/refresh")
    alerts = client.get("/api/alerts").json()["alerts"]
    assert next(a for a in alerts if a["kind"] == "venta")["resolved_at"] is not None
    rev = next(a for a in alerts if a["kind"] == "revision")
    assert rev["severity"] == "warning" and rev["resolved_at"] is None

    assert client.post(f"/api/alerts/{rev['id']}/read").json()["updated"] == 1
    client.post("/api/alerts/read-all")
    assert client.get("/api/alerts", params={"unread": True}).json()["alerts"] == []
    t = client.post("/api/alerts/test").json()
    assert t["sent"] == ["telegram"] and t["alert"]["kind"] == "prueba"


def test_signal_change_alert(env):
    client, _, notifier = env
    client.post("/api/refresh")
    md = client.app.state.md
    today = max(r.date for r in md.Session().query(SignalRow).filter(SignalRow.symbol == "SPY"))
    cur_label = md.Session().get(SignalRow, ("SPY", today)).label
    other = "VENDER" if cur_label != "VENDER" else "COMPRAR"
    with md.Session() as s:
        s.add(SignalRow(symbol="SPY", date=today - timedelta(days=3), computed_at=datetime.now(timezone.utc),
                        label=other, score=0, confidence=0, provisional=False, summary="", payload="{}"))
        s.commit()
    new = client.app.state.alerts.evaluate()
    sig = [a for a in new if a["kind"] == "senal"]
    assert len(sig) == 1 and f"de {other} a {cur_label}" in sig[0]["title"]
    again = client.app.state.alerts.evaluate()
    assert not [a for a in again if a["kind"] == "senal"]  # una sola vez por símbolo, día y etiqueta


def test_data_error_alert(env):
    client, prov, _ = env
    client.post("/api/refresh")
    prov.fail_with = ProviderError("caído")
    client.post("/api/refresh")
    md = client.app.state.md
    with md.Session() as s:  # el último precio bueno es de hace 2 horas
        q = s.get(LatestQuote, "SPY")
        q.fetched_at = datetime(2026, 11, 23, 14, 0, tzinfo=timezone.utc)
        s.commit()
    market_open = datetime(2026, 11, 23, 16, 0, tzinfo=timezone.utc)  # NYSE abierto
    new = client.app.state.alerts._data_errors(market_open)
    assert [a["symbol"] for a in new] == ["SPY"] and new[0]["severity"] == "info"
    assert client.app.state.alerts._data_errors(market_open) == []  # no se repite


# --- Canales externos ------------------------------------------------------------------

@respx.mock
def test_notifier_telegram_and_email():
    route = respx.post("https://api.telegram.org/botTOKEN/sendMessage").respond(json={"ok": True})
    sent_mail = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            sent_mail.append(msg)

    secrets = Secrets(telegram_bot_token="TOKEN", telegram_chat_id="123", smtp_host="smtp.x", smtp_user="u",
                      smtp_password="p", alert_email_to="yo@ejemplo.cl", _env_file=None)
    n = Notifier(secrets, ["telegram", "email"], http=httpx.Client(), smtp_factory=FakeSMTP)
    assert n.configured() == {"telegram": True, "email": True}
    sent, errors = n.send("Título", "Mensaje")
    assert sent == ["telegram", "email"] and errors == []
    assert "no es asesoría financiera" in route.calls[0].request.content.decode()
    assert sent_mail[0]["To"] == "yo@ejemplo.cl" and sent_mail[0]["Subject"] == "[Análisis] Título"

    route.respond(status_code=401)
    sent, errors = n.send("T", "M")
    assert sent == ["email"] and "telegram" in errors[0]


def test_notifier_without_credentials_does_nothing():
    n = Notifier(Secrets(_env_file=None), ["telegram", "email"])
    assert n.configured() == {"telegram": False, "email": False}
    assert n.send("T", "M") == ([], [])
