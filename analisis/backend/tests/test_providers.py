from datetime import datetime, timezone

import httpx
import pandas as pd
import pytest
import respx

from app.providers.alphavantage import AlphaVantageProvider
from app.providers.base import MissingApiKey, NotSupported, ProviderError, RateLimited, SymbolNotFound, market_of, normalize_history
from app.providers.finnhub import FinnhubProvider
from app.providers.fx import FxService, MindicadorFx
from app.providers.twelvedata import TwelveDataProvider
from app.providers.yfinance_provider import YFinanceProvider


def test_market_of():
    assert market_of("SQM-B.SN") == "CL"
    assert market_of("spy") == "US"
    assert market_of("CLP=X") == "FX"


def test_normalize_history_tz_dupes_and_adj_close():
    idx = pd.DatetimeIndex(["2026-11-20 00:00-05:00", "2026-11-23 00:00-05:00", "2026-11-23 00:00-05:00"])
    raw = pd.DataFrame({"Open": [1, 2, 3], "High": [1, 2, 3], "Low": [1, 2, 3], "Close": [1, 2, 3],
                        "Adj Close": [0.9, 1.9, 2.9], "Volume": [10, 20, 30]}, index=idx)
    out = normalize_history(raw)
    assert list(out.columns) == ["open", "high", "low", "close", "volume", "adj_close"]
    assert out.index.tz is None and len(out) == 2 and out["close"].iloc[-1] == 3


# --- yfinance (con un Ticker falso) ------------------------------------------
class FakeTicker:
    def __init__(self, df, meta=None, exc=None):
        self.df, self.history_metadata, self.exc = df, meta or {}, exc

    def history(self, **kwargs):
        if self.exc:
            raise self.exc
        return self.df


def _yahoo_df(dates, closes, tz="America/Santiago"):
    idx = pd.DatetimeIndex(pd.to_datetime(dates)).tz_localize(tz)
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes, "Adj Close": closes,
                         "Volume": [100] * len(closes)}, index=idx)


def test_yfinance_quote_prev_close_comes_from_series():
    df = _yahoo_df(["2026-11-19", "2026-11-20", "2026-11-23"], [100, 110, 121])
    meta = {"regularMarketPrice": 121.0, "regularMarketTime": 1763924477,  # 2025-11-23 16:01 CLT aprox.
            "chartPreviousClose": 50.0, "currency": "CLP", "exchangeTimezoneName": "America/Santiago",
            "longName": "Prueba SA", "fullExchangeName": "Santiago"}
    meta["regularMarketTime"] = int(datetime(2026, 11, 23, 19, 1, tzinfo=timezone.utc).timestamp())
    p = YFinanceProvider(lambda s: FakeTicker(df, meta))
    q = p.get_quote("TEST.SN")
    assert q.price == 121 and q.prev_close == 110  # no usa chartPreviousClose (cierre previo al rango)
    assert q.change_pct == pytest.approx(10.0)
    assert q.name == "Prueba SA" and q.realtime is False and q.volume == 100


def test_yfinance_quote_before_today_session():
    # Antes de la apertura, el último precio es el de ayer y el cierre anterior es el de anteayer.
    df = _yahoo_df(["2026-11-19", "2026-11-20"], [100, 110])
    meta = {"regularMarketPrice": 110.0, "exchangeTimezoneName": "America/Santiago",
            "regularMarketTime": int(datetime(2026, 11, 20, 19, 0, tzinfo=timezone.utc).timestamp())}
    q = YFinanceProvider(lambda s: FakeTicker(df, meta)).get_quote("X.SN")
    assert q.prev_close == 100 and q.day_open == 110


def test_yfinance_errors_are_mapped():
    empty = pd.DataFrame()
    with pytest.raises(SymbolNotFound):
        YFinanceProvider(lambda s: FakeTicker(empty)).get_quote("NOPE")
    with pytest.raises(SymbolNotFound):
        YFinanceProvider(lambda s: FakeTicker(None, exc=Exception("NOPE: possibly delisted; no price data found"))).get_quote("NOPE")
    with pytest.raises(RateLimited):
        YFinanceProvider(lambda s: FakeTicker(None, exc=Exception("Too Many Requests. Rate limited."))).get_quote("SPY")
    with pytest.raises(ProviderError):
        YFinanceProvider(lambda s: FakeTicker(None, exc=ConnectionError("boom"))).get_quote("SPY")


def test_yfinance_history():
    df = _yahoo_df(["2026-11-19", "2026-11-20"], [100, 110], tz="America/New_York")
    out = YFinanceProvider(lambda s: FakeTicker(df)).get_history("SPY")
    assert str(out.index[0].date()) == "2026-11-19" and "adj_close" in out.columns


# --- Finnhub -----------------------------------------------------------------
@respx.mock
def test_finnhub_quote():
    respx.get("https://finnhub.io/api/v1/quote").respond(
        json={"c": 722.5, "d": 1, "dp": 1, "h": 728, "l": 720, "o": 724, "pc": 689.8, "t": 1763931600})
    q = FinnhubProvider("KEY").get_quote("SPY")
    assert q.price == 722.5 and q.prev_close == 689.8 and q.realtime is True and q.source == "finnhub"


@respx.mock
def test_finnhub_unknown_symbol_and_429():
    route = respx.get("https://finnhub.io/api/v1/quote")
    route.respond(json={"c": 0, "d": None, "dp": None, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0})
    with pytest.raises(SymbolNotFound):
        FinnhubProvider("KEY").get_quote("XXXX")
    route.respond(status_code=429)
    with pytest.raises(RateLimited):
        FinnhubProvider("KEY").get_quote("SPY")


def test_finnhub_needs_key_and_has_no_history():
    p = FinnhubProvider("")
    assert not p.is_configured()
    with pytest.raises(MissingApiKey):
        p.get_quote("SPY")
    with pytest.raises(NotSupported):
        FinnhubProvider("K").get_history("SPY")


@respx.mock
def test_network_error_becomes_provider_error():
    respx.get("https://finnhub.io/api/v1/quote").mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(ProviderError):
        FinnhubProvider("KEY").get_quote("SPY")


# --- Twelve Data -------------------------------------------------------------
@respx.mock
def test_twelvedata_quote_and_history():
    respx.get("https://api.twelvedata.com/quote").respond(json={
        "symbol": "VOO", "name": "Vanguard S&P 500 ETF", "exchange": "NYSE", "currency": "USD",
        "timestamp": 1763931600, "open": "660", "high": "666", "low": "659", "close": "664.18",
        "volume": "5000000", "previous_close": "635.5"})
    respx.get("https://api.twelvedata.com/time_series").respond(json={
        "meta": {"symbol": "VOO"}, "status": "ok",
        "values": [{"datetime": "2026-11-20", "open": "640", "high": "641", "low": "630", "close": "635.5", "volume": "1"},
                   {"datetime": "2026-11-23", "open": "660", "high": "666", "low": "659", "close": "664.18", "volume": "2"}]})
    p = TwelveDataProvider("KEY")
    q = p.get_quote("VOO")
    assert q.price == 664.18 and q.prev_close == 635.5 and q.name.startswith("Vanguard")
    h = p.get_history("VOO")
    assert len(h) == 2 and h["close"].iloc[-1] == 664.18


@respx.mock
def test_twelvedata_errors_in_body():
    route = respx.get("https://api.twelvedata.com/quote")
    route.respond(json={"code": 429, "message": "You have run out of API credits", "status": "error"})
    with pytest.raises(RateLimited):
        TwelveDataProvider("KEY").get_quote("VOO")
    route.respond(json={"code": 404, "message": "symbol not found", "status": "error"})
    with pytest.raises(SymbolNotFound):
        TwelveDataProvider("KEY").get_quote("NOPE")


# --- Alpha Vantage -----------------------------------------------------------
@respx.mock
def test_alphavantage_quote_history_and_limit():
    route = respx.get("https://www.alphavantage.co/query")
    route.respond(json={"Global Quote": {
        "01. symbol": "EEM", "02. open": "50", "03. high": "51", "04. low": "49.5", "05. price": "50.7",
        "06. volume": "1000", "07. latest trading day": "2026-11-23", "08. previous close": "50.1"}})
    p = AlphaVantageProvider("KEY")
    q = p.get_quote("EEM")
    assert q.price == 50.7 and q.as_of.hour == 21  # 16:00 Nueva York = 21:00 UTC en noviembre
    route.respond(json={"Time Series (Daily)": {
        "2026-11-23": {"1. open": "50", "2. high": "51", "3. low": "49.5", "4. close": "50.7", "5. volume": "1000"},
        "2026-11-20": {"1. open": "49", "2. high": "50", "3. low": "48", "4. close": "50.1", "5. volume": "900"}}})
    h = p.get_history("EEM")
    assert list(h.index.strftime("%Y-%m-%d")) == ["2026-11-20", "2026-11-23"]
    route.respond(json={"Information": "We have detected your API key ... standard API rate limit is 25 requests per day."})
    with pytest.raises(RateLimited):
        p.get_quote("EEM")


# --- Dólar -------------------------------------------------------------------
@respx.mock
def test_mindicador_and_fallback():
    respx.get("https://mindicador.cl/api/dolar").respond(json={"serie": [
        {"fecha": "2026-11-23T03:00:00.000Z", "valor": 1015.3}, {"fecha": "2026-11-21T03:00:00.000Z", "valor": 1021.0}]})
    fx = MindicadorFx().latest()
    assert fx.rate == 1015.3 and fx.prev == 1021.0 and fx.kind == "observado"

    class Down:
        name = "mindicador"

        def latest(self):
            raise ProviderError("timeout")

    class Up:
        name = "yfinance"

        def latest(self):
            return fx

    svc = FxService([Down(), Up()])
    assert svc.latest() is fx and "mindicador" in svc.last_errors
    with pytest.raises(ProviderError):
        FxService([Down()]).latest()
