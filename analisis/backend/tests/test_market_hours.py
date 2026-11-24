from datetime import datetime, timedelta, timezone

from app.market_hours import freshness, is_open, session_info
from app.scheduler import due_symbols

UTC = timezone.utc


def t(*args):
    return datetime(*args, tzinfo=UTC)


def test_santiago_and_new_york_sessions():
    # Lunes 23/11/2026 (horario de verano chileno, UTC-3): abierto a media sesión, cerrado de madrugada.
    assert is_open("CL", t(2026, 11, 23, 15, 0))
    assert not is_open("CL", t(2026, 11, 23, 5, 0))
    assert is_open("US", t(2026, 11, 23, 16, 0))
    assert not is_open("US", t(2026, 11, 23, 22, 0))


def test_weekend_and_holidays():
    assert not is_open("CL", t(2026, 11, 22, 15, 0))  # domingo
    assert not is_open("US", t(2026, 11, 26, 16, 0))  # Acción de Gracias: NYSE cerrado
    assert is_open("CL", t(2026, 11, 26, 15, 0))      # ...pero Santiago abre
    assert not is_open("US", t(2026, 11, 27, 19, 0))  # viernes siguiente: cierre anticipado 13:00 NY


def test_fx_follows_santiago_hours():
    assert is_open("FX", t(2026, 11, 23, 15, 0))
    assert not is_open("FX", t(2026, 11, 23, 23, 0))  # noche: el USD/CLP casi no se mueve
    assert not is_open("FX", t(2026, 11, 21, 15, 0))  # sábado


def test_session_info():
    s = session_info("US", t(2026, 11, 23, 16, 0))
    assert s["open"] and s["closes_at"].startswith("2026-11-23T21:00")
    s = session_info("US", t(2026, 11, 25, 22, 0))
    assert not s["open"] and s["next_open"].startswith("2026-11-27T14:30")


def test_freshness_labels():
    now = t(2026, 11, 23, 15, 0)
    f = freshness(now - timedelta(minutes=1), realtime=True, market_open=True, now=now)
    assert f["kind"] == "tiempo_real" and f["label"] == "Tiempo real · 11:59"  # 14:59 UTC = 11:59 en Santiago (verano, UTC-3)
    f = freshness(now - timedelta(minutes=16), realtime=False, market_open=True, now=now)
    assert f["kind"] == "retraso" and "hace 16 min" in f["label"]
    f = freshness(now - timedelta(minutes=5), realtime=True, market_open=True, now=now)
    assert f["kind"] == "retraso"  # se declara tiempo real pero el dato tiene 5 min
    f = freshness(now - timedelta(hours=2), realtime=False, market_open=True, now=now, stale_after_minutes=30)
    assert f["kind"] == "desactualizado"
    f = freshness(now - timedelta(days=3), realtime=False, market_open=False, now=now)
    assert f["kind"] == "cierre" and "20/11" in f["label"]
    assert freshness(None, realtime=False, market_open=True, now=now)["kind"] == "sin_datos"
    f = freshness(t(2026, 11, 23, 3, 0), realtime=False, market_open=True, now=now, daily=True)
    assert f["kind"] == "diario" and f["label"] == "Dato diario oficial del 23/11"


def test_due_symbols_open_vs_closed():
    now = t(2026, 11, 23, 15, 0)
    open_fn = lambda m, _now: m == "US"  # noqa: E731
    last = {"SPY": now - timedelta(seconds=30), "VOO": now - timedelta(seconds=90),
            "SQM-B.SN": now - timedelta(minutes=30), "CHILE.SN": now - timedelta(hours=2)}
    due = due_symbols(["SPY", "VOO", "SQM-B.SN", "CHILE.SN", "EEM"], last, now, 60, 3600, open_fn)
    assert due == ["VOO", "CHILE.SN", "EEM"]
