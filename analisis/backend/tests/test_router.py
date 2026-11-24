import pytest

from app.providers.base import ProviderError, RateLimited, RateLimiter, SymbolNotFound
from app.providers.router import AllProvidersFailed, ProviderRouter

from .conftest import FakeProvider


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(primary, backup, clock=None, limiters=None):
    order = {"US": [primary.name, backup.name], "CL": [primary.name, backup.name]}
    return ProviderRouter({primary.name: primary, backup.name: backup}, order, order, limiters, clock=clock or Clock())


def test_uses_first_provider_when_healthy():
    a, b = FakeProvider("a"), FakeProvider("b")
    assert make(a, b).get_quote("SPY").source == "a"
    assert b.calls == []


def test_falls_back_and_records_health():
    a, b = FakeProvider("a"), FakeProvider("b")
    a.fail_with = ProviderError("caído")
    r = make(a, b)
    assert r.get_quote("SPY").source == "b"
    st = {p["name"]: p for p in r.status()}
    assert st["a"]["consecutive_failures"] == 1 and "caído" in st["a"]["last_error"]
    assert st["b"]["last_ok"] is not None


def test_skips_unconfigured_and_wrong_market():
    a = FakeProvider("a", configured=False)
    b = FakeProvider("b", markets=("US",))
    r = make(a, b)
    assert r.get_quote("SPY").source == "b"
    with pytest.raises(AllProvidersFailed):
        r.get_quote("SQM-B.SN")  # nadie configurado cubre Chile


def test_history_skips_providers_without_history():
    a, b = FakeProvider("a", history=False), FakeProvider("b")
    r = make(a, b)
    df = r.get_history("SPY")
    assert df.attrs["source"] == "b" and ("history", "SPY") not in a.calls


def test_symbol_not_found_everywhere():
    r = make(FakeProvider("a"), FakeProvider("b"))
    with pytest.raises(SymbolNotFound):
        r.get_quote("NOEXISTE")
    # No castiga a los proveedores: un símbolo inexistente no es una falla.
    assert all(p["consecutive_failures"] == 0 for p in r.status())


def test_not_found_plus_outage_is_not_reported_as_not_found():
    a, b = FakeProvider("a"), FakeProvider("b")
    b.fail_with = ProviderError("caído")
    with pytest.raises(AllProvidersFailed):
        make(a, b).get_quote("NOEXISTE")


def test_cooldown_after_repeated_failures():
    clock = Clock()
    a, b = FakeProvider("a"), FakeProvider("b")
    a.fail_with = ProviderError("caído")
    r = make(a, b, clock)
    for _ in range(3):
        r.get_quote("SPY")
    assert len([c for c in a.calls if c[0] == "quote"]) == 3
    r.get_quote("SPY")  # en pausa: ni siquiera se le pregunta
    assert len([c for c in a.calls if c[0] == "quote"]) == 3
    a.fail_with = None
    clock.t += 61
    assert r.get_quote("SPY").source == "a"  # pasada la pausa se reintenta y se recupera
    assert {p["name"]: p for p in r.status()}["a"]["consecutive_failures"] == 0


def test_provider_rate_limit_triggers_immediate_cooldown():
    clock = Clock()
    a, b = FakeProvider("a"), FakeProvider("b")
    a.fail_with = RateLimited("429")
    r = make(a, b, clock)
    r.get_quote("SPY")
    r.get_quote("SPY")
    assert len(a.calls) == 1


def test_own_rate_limiter_falls_back_without_penalty():
    clock = Clock()
    a, b = FakeProvider("a"), FakeProvider("b")
    r = make(a, b, clock, limiters={"a": RateLimiter(per_minute=2, clock=clock)})
    sources = [r.get_quote("SPY").source for _ in range(3)]
    assert sources == ["a", "a", "b"]
    assert {p["name"]: p for p in r.status()}["a"]["consecutive_failures"] == 0
    clock.t += 60
    assert r.get_quote("SPY").source == "a"


def test_rate_limiter_windows():
    clock = Clock()
    rl = RateLimiter(per_minute=2, per_day=3, clock=clock)
    rl.acquire(); rl.acquire()
    with pytest.raises(RateLimited):
        rl.acquire()
    clock.t += 60
    rl.acquire()
    clock.t += 60
    with pytest.raises(RateLimited, match="día"):
        rl.acquire()
    assert rl.usage()["last_day"] == 3
    clock.t += 86400
    rl.acquire()
