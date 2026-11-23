from datetime import date

import pytest

from app.portfolio import (
    Candidate,
    InvalidTransaction,
    PriceInfo,
    Tx,
    build_positions,
    risk_status,
    suggest_contribution,
    value_portfolio,
)

D = date(2026, 1, 5)
TARGET = {"CL": 0.30, "INTL": 0.70}


def tx(symbol, side, qty, price, cur="USD", d=D, fee=0.0, fx=900.0, id=None):
    return Tx(symbol, side, qty, price, cur, d, fee, fx, id)


def test_weighted_average_cost_with_fees():
    p = build_positions([tx("SPY", "compra", 10, 100, fee=10), tx("SPY", "compra", 10, 200, d=date(2026, 2, 1))])["SPY"]
    assert p.quantity == 20
    assert p.cost_native == pytest.approx(3010)
    assert p.avg_cost == pytest.approx(150.5)


def test_realized_gain_on_sale():
    txs = [tx("SPY", "compra", 10, 100, fx=900), tx("SPY", "venta", 4, 150, fee=2, fx=1000, d=date(2026, 3, 1))]
    p = build_positions(txs)["SPY"]
    assert p.quantity == 6
    assert p.realized_native == pytest.approx(4 * 150 - 2 - 400)
    # En CLP: ingreso con el dólar de la venta, costo con el de la compra.
    assert p.realized_clp == pytest.approx((600 - 2) * 1000 - 400 * 900)
    assert p.cost_native == pytest.approx(600) and p.cost_clp == pytest.approx(600 * 900)


def test_full_sale_closes_position():
    p = build_positions([tx("SPY", "compra", 5, 100), tx("SPY", "venta", 5, 110, d=date(2026, 2, 1))])["SPY"]
    assert p.quantity == 0 and p.avg_cost is None and p.cost_clp == 0


def test_cannot_sell_more_than_held():
    with pytest.raises(InvalidTransaction, match="cortos"):
        build_positions([tx("SPY", "compra", 5, 100), tx("SPY", "venta", 6, 100, d=date(2026, 2, 1))])
    with pytest.raises(InvalidTransaction):
        build_positions([tx("SPY", "venta", 1, 100)])


def test_same_day_buy_is_processed_before_sell():
    p = build_positions([tx("SPY", "venta", 5, 110, id=2), tx("SPY", "compra", 5, 100, id=1)])["SPY"]
    assert p.quantity == 0


def test_valuation_splits_price_and_fx_effect():
    pos = build_positions([tx("SPY", "compra", 10, 100, fx=900),
                           tx("SQM-B.SN", "compra", 100, 50000, cur="CLP", fx=900)])
    prices = {"SPY": PriceInfo(110, 100, "USD"), "SQM-B.SN": PriceInfo(40000, 41000, "CLP")}
    out = value_portfolio(pos, prices, fx_now=1000, target=TARGET, review_pct=-20, sell_pct=-25)
    spy = next(r for r in out["positions"] if r["symbol"] == "SPY")
    assert spy["value_clp"] == pytest.approx(10 * 110 * 1000)
    assert spy["unrealized_clp"] == pytest.approx(1_100_000 - 900_000)
    assert spy["price_effect_clp"] == pytest.approx(100 * 1000)     # +10 USD por acción, al dólar de hoy
    assert spy["fx_effect_clp"] == pytest.approx(1000 * (1000 - 900))  # el dólar subió 100
    assert spy["drawdown_pct"] == pytest.approx(10) and spy["status"] == "ok"
    sqm = next(r for r in out["positions"] if r["symbol"] == "SQM-B.SN")
    assert sqm["drawdown_pct"] == pytest.approx(-20) and sqm["status"] == "revisar"
    assert sqm["day_change_clp"] == pytest.approx(100 * -1000)
    t = out["totals"]
    assert t["value_clp"] == pytest.approx(1_100_000 + 4_000_000)
    assert t["value_usd"] == pytest.approx(5_100)
    alloc = {a["bucket"]: a for a in out["allocation"]}
    assert alloc["CL"]["pct"] == pytest.approx(4_000_000 / 5_100_000 * 100)
    assert alloc["CL"]["target_pct"] == 30
    assert sum(r["weight_pct"] for r in out["positions"]) == pytest.approx(100)


def test_missing_price_is_reported_not_crashing():
    pos = build_positions([tx("SPY", "compra", 1, 100)])
    out = value_portfolio(pos, {}, fx_now=950, target=TARGET, review_pct=-20, sell_pct=-25)
    assert out["missing_prices"] == ["SPY"] and out["positions"][0]["status"] == "sin_precio"
    assert out["totals"]["value_clp"] == 0


def test_risk_status_thresholds():
    assert risk_status(-19.9, -20, -25) == "ok"
    assert risk_status(-20, -20, -25) == "revisar"
    assert risk_status(-25, -20, -25) == "vender"
    assert risk_status(None, -20, -25) == "sin_precio"


# --- Aportes -------------------------------------------------------------------------

def cands():
    return [
        Candidate("SQM-B.SN", 50000, "CLP", 50000, "MANTENER", held=True),
        Candidate("CHILE.SN", 190, "CLP", 190, "COMPRAR"),
        Candidate("SPY", 700, "USD", 700 * 950, "MANTENER", held=True),
        Candidate("VOO", 650, "USD", 650 * 950, "VENDER"),
        Candidate("EEM", 58, "USD", 58 * 950, None),
    ]


def test_contribution_goes_to_underweight_bucket():
    # Cartera 100 % internacional: todo el aporte que quepa va a Chile.
    r = suggest_contribution(1_000_000, {"CL": 0, "INTL": 10_000_000}, cands(), TARGET, {"CL": False, "US": True})
    b = {x["bucket"]: x for x in r.buckets}
    assert b["INTL"]["amount_clp"] == 0
    assert b["CL"]["amount_clp"] > 990_000  # casi todo invertido (acciones enteras)
    assert b["CL"]["after_pct"] > b["CL"]["current_pct"]
    syms = {line.symbol for line in r.lines}
    assert syms <= {"SQM-B.SN", "CHILE.SN"}
    for line in r.lines:
        assert line.quantity == int(line.quantity)  # acciones chilenas enteras
    assert "nunca se sugiere vender" in " ".join(r.notes)


def test_contribution_splits_by_deficit_and_signal_weights():
    # Ya en objetivo: el aporte se reparte 30/70.
    r = suggest_contribution(10_000_000, {"CL": 3_000_000, "INTL": 7_000_000}, cands(), TARGET,
                             {"CL": True, "US": True})
    b = {x["bucket"]: x for x in r.buckets}
    assert b["CL"]["amount_clp"] == pytest.approx(3_000_000, rel=0.01)
    assert b["INTL"]["amount_clp"] == pytest.approx(7_000_000, rel=0.01)
    lines = {line.symbol: line for line in r.lines}
    # COMPRAR pesa el doble que MANTENER dentro de Chile.
    assert lines["CHILE.SN"].amount_clp == pytest.approx(2 * lines["SQM-B.SN"].amount_clp, rel=0.01)
    # VENDER y sin señal no reciben aporte, y se informa por qué.
    assert "VOO" not in lines and "EEM" not in lines
    reasons = {e["symbol"]: e["reason"] for e in r.excluded}
    assert reasons == {"VOO": "señal VENDER", "EEM": "sin señal (falta historia)"}
    assert all(abs(x - round(x, 4)) < 1e-9 for x in (line.quantity for line in r.lines))


def test_contribution_whole_shares_leftover_note():
    only = [Candidate("SQM-B.SN", 50000, "CLP", 50000, "MANTENER")]
    r = suggest_contribution(120_000, {"CL": 0, "INTL": 0}, only, {"CL": 1.0, "INTL": 0.0}, {"CL": False})
    assert r.lines[0].quantity == 2 and r.lines[0].amount_clp == 100_000
    assert r.unallocated_clp == 20_000
    assert any("sobran" in n for n in r.notes)


def test_contribution_no_eligible_in_bucket():
    only = [Candidate("SPY", 700, "USD", 665_000, "MANTENER")]
    r = suggest_contribution(1_000_000, {"CL": 0, "INTL": 0}, only, TARGET, {"US": True})
    # 300.000 de Chile + unos pesos por redondear las fracciones a 4 decimales
    assert r.unallocated_clp == pytest.approx(300_000, abs=100)
    assert any("Chile: no hay símbolos elegibles" in n for n in r.notes)


def test_contribution_rejects_non_positive():
    with pytest.raises(ValueError):
        suggest_contribution(0, {}, [], TARGET, {})


def test_contribution_respects_risk_rules():
    """Una posición en zona de venta no recibe aporte aunque su señal técnica sea MANTENER o COMPRAR."""
    cs = [
        Candidate("SQM-B.SN", 50000, "CLP", 50000, "COMPRAR", held=True, risk="vender", drawdown_pct=-28.9),
        Candidate("CHILE.SN", 190, "CLP", 190, "MANTENER", held=True, risk="revisar", drawdown_pct=-21.0),
    ]
    r = suggest_contribution(300_000, {"CL": 0, "INTL": 0}, cs, {"CL": 1.0, "INTL": 0.0}, {"CL": False})
    assert [line.symbol for line in r.lines] == ["CHILE.SN"]
    assert "en revisión (-21,0 %" in r.lines[0].reason
    assert r.excluded == [{"symbol": "SQM-B.SN", "signal": "COMPRAR", "reason": "caída de -28,9 % bajo el umbral de venta"}]
