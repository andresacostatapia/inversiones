import numpy as np
import pandas as pd
import pytest

from app import indicators as ind
from app.config import SignalsConfig
from app.signals import BUY, HOLD, SELL, evaluate, label_for, score_frame, score_history, signal_for_bars

from .conftest import make_bars
from .test_strategies import trending

CFG = SignalsConfig()


def test_label_thresholds():
    assert label_for(30, CFG) == BUY and label_for(29.9, CFG) == HOLD
    assert label_for(-30, CFG) == SELL and label_for(None, CFG) is None
    custom = SignalsConfig(thresholds={"buy": 50, "sell": -10})
    assert label_for(40, custom) == HOLD and label_for(-10, custom) == SELL


def test_composite_is_weighted_average():
    df = ind.compute_all(make_bars(400, seed=5))
    cfg = SignalsConfig(volume_effect=0, weights={"trend": 3, "rsi": 1, "macd": 0, "bollinger": 0})
    f = score_frame(df, cfg)
    expected = (3 * f["trend"] + f["rsi"]) / 4
    pd.testing.assert_series_equal(f["composite"].iloc[250:], expected.iloc[250:].clip(-100, 100), check_names=False)
    assert "macd" not in f.columns  # peso 0 = desactivada


def test_missing_strategy_renormalizes_weights():
    df = ind.compute_all(make_bars(120, seed=5))  # sin SMA200: tendencia no opina
    f = score_frame(df, SignalsConfig(volume_effect=0))
    row = f.iloc[-1]
    assert np.isnan(row["trend"])
    assert row["composite"] == pytest.approx(np.mean([row["rsi"], row["macd"], row["bollinger"]]))
    assert row["available_weight"] == pytest.approx(0.75)


def _with_volume(rel_volume, day_up=True):
    df = ind.compute_all(trending(drift=0.002))
    df.loc[df.index[-1], "rel_volume"] = rel_volume
    prev = df["close"].iloc[-2]
    df.loc[df.index[-1], "close"] = prev * (1.01 if day_up else 0.99)
    return df


def test_volume_confirms_or_weakens():
    cfg = SignalsConfig(volume_effect=0.2)
    f_conf = score_frame(_with_volume(2.0, day_up=True), cfg).iloc[-1]
    assert f_conf["raw"] > 0 and f_conf["volume_factor"] == pytest.approx(1.2)
    f_against = score_frame(_with_volume(2.0, day_up=False), cfg).iloc[-1]
    assert f_against["volume_factor"] == pytest.approx(0.8)
    f_low = score_frame(_with_volume(0.5), cfg).iloc[-1]
    assert f_low["volume_factor"] == pytest.approx(0.9)
    f_prov = score_frame(_with_volume(2.0), cfg, provisional_last=True).iloc[-1]
    assert f_prov["volume_factor"] == 1.0  # volumen del día incompleto: no se usa


def test_strong_uptrend_signal_has_reasons_and_actions():
    r = signal_for_bars("UP", trending(drift=0.002), CFG)
    assert r.label in (BUY, HOLD)
    assert r.strategies[0].key == "trend" and r.strategies[0].score > 80
    assert any("SMA200" in x for x in r.reasons)
    assert r.actions["holding"] and r.actions["not_holding"]
    assert 0 <= r.confidence <= 100
    assert r.stop_reference < float(trending(drift=0.002)["close"].iloc[-1])


def test_buy_signal_when_all_agree():
    """Construye un día donde todas las estrategias apuntan a comprar."""
    df = ind.compute_all(trending(drift=0.002))
    i = df.index[-1]
    df.loc[i, ["rsi14", "bb_pctb", "macd", "macd_signal"]] = [25, -0.1, 2.0, 1.0]
    df.loc[df.index[-2], ["macd", "macd_signal"]] = [0.5, 1.0]  # cruce MACD alcista hoy
    r = evaluate("X", df, SignalsConfig(volume_effect=0))
    assert r.label == BUY and r.score > 80
    assert r.confidence > 80
    # El resumen toma la primera razón de las dos estrategias que más empujan.
    assert r.summary == "Cruce MACD alcista hoy + Precio bajo la banda inferior de Bollinger (%B -0,10)"


def test_confidence_drops_with_disagreement_and_stale_data():
    df = ind.compute_all(trending(drift=0.002))
    i = df.index[-1]
    df.loc[i, ["rsi14", "bb_pctb"]] = [25, -0.1]
    agree = evaluate("X", df, SignalsConfig(volume_effect=0))
    df2 = df.copy()
    df2.loc[i, ["rsi14"]] = [80]  # el RSI ahora contradice
    mixed = evaluate("X", df2, SignalsConfig(volume_effect=0))
    assert mixed.confidence < agree.confidence
    stale = evaluate("X", df, SignalsConfig(volume_effect=0), stale=True)
    assert stale.confidence < agree.confidence
    assert any("desactualizado" in w for w in stale.warnings)


def test_provisional_signal_warns():
    r = signal_for_bars("X", trending(), CFG, provisional=True)
    assert r.provisional and "provisional" in r.warnings[0]


def test_hold_summary_mentions_mixed_signals():
    df = ind.compute_all(trending(drift=0.002))
    df.loc[df.index[-1], ["rsi14", "bb_pctb"]] = [85, 1.2]  # tendencia alcista pero sobrecompra fuerte
    r = evaluate("X", df, SignalsConfig(volume_effect=0))
    assert r.label == HOLD
    assert r.summary.startswith("Señales mixtas: RSI 85: sobrecompra vs. Precio")


def test_not_enough_history():
    r = signal_for_bars("NEW", make_bars(15), CFG)  # solo el RSI tiene datos: menos de la mitad del peso
    assert r.label is None and r.score is None and "Sin datos" in r.summary


def test_score_history_shape():
    h = score_history(make_bars(300), CFG)
    assert {"trend", "rsi", "macd", "bollinger", "composite", "label"} <= set(h.columns)
    assert set(h["label"].dropna()) <= {BUY, HOLD, SELL}
