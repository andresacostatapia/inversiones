"""Servicio de datos: pide precios/historia a los proveedores y los guarda en SQLite."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert

from .config import AppConfig
from .db import FxRateRow, Instrument, LatestQuote, PriceBar, WatchlistItem
from .market_hours import freshness, is_open
from .providers.base import Quote, SymbolNotFound, market_of, utcnow
from .providers.fx import PAIR, FxService
from .providers.router import ProviderRouter

log = logging.getLogger(__name__)

EXCHANGE_TZ = {"CL": "America/Santiago", "US": "America/New_York", "FX": "UTC"}
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-=^]{0,19}$")
# Las velas armadas con el precio en vivo llevan este sufijo en `source` hasta que la historia oficial las reemplaza.
LIVE_SUFFIX = ":vivo"


class InvalidSymbol(ValueError):
    pass


def normalize_symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not SYMBOL_RE.match(s):
        raise InvalidSymbol(f"símbolo inválido: {raw!r}")
    return s


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite no guarda la zona horaria: todo se guarda en UTC y se relee como UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def session_date(as_of: datetime, market: str) -> date:
    return as_utc(as_of).astimezone(ZoneInfo(EXCHANGE_TZ.get(market, "UTC"))).date()


class MarketData:
    def __init__(self, cfg: AppConfig, session_factory, router: ProviderRouter, fx: FxService):
        self.cfg = cfg
        self.Session = session_factory
        self.router = router
        self.fx = fx
        self.last_refresh: dict[str, datetime] = {}

    # --- watchlist ----------------------------------------------------------
    def startup(self) -> None:
        self.seed_watchlist()
        self.purge_placeholder_bars()

    def seed_watchlist(self) -> None:
        """La primera vez, copia la watchlist de config.yaml a la base de datos."""
        with self.Session() as s:
            if s.scalar(select(func.count()).select_from(WatchlistItem)):
                return
            for i, raw in enumerate(self.cfg.watchlist):
                try:
                    s.add(WatchlistItem(symbol=normalize_symbol(raw), position=i, added_at=utcnow()))
                except InvalidSymbol:
                    log.warning("símbolo inválido en config.yaml: %s", raw)
            s.commit()

    def watchlist(self) -> list[str]:
        with self.Session() as s:
            return list(s.scalars(select(WatchlistItem.symbol).order_by(WatchlistItem.position, WatchlistItem.symbol)))

    def add_symbol(self, raw: str, load_history: bool = True) -> dict:
        """Valida el símbolo pidiendo su precio; si no existe lanza SymbolNotFound y no se agrega."""
        symbol = normalize_symbol(raw)
        quote = self.router.get_quote(symbol)
        self._store_quote(quote)
        with self.Session() as s:
            if s.get(WatchlistItem, symbol) is None:
                pos = (s.scalar(select(func.max(WatchlistItem.position))) or 0) + 1
                s.add(WatchlistItem(symbol=symbol, position=pos, added_at=utcnow()))
                s.commit()
        if load_history:
            try:
                self.refresh_history(symbol)
            except Exception as e:  # el precio ya quedó; la historia se reintenta en el próximo ciclo
                log.warning("historia de %s no disponible: %s", symbol, e)
        return self.quote_view(symbol)

    def remove_symbol(self, raw: str) -> bool:
        symbol = normalize_symbol(raw)
        with self.Session() as s:
            n = s.execute(delete(WatchlistItem).where(WatchlistItem.symbol == symbol)).rowcount
            s.commit()
        return bool(n)

    # --- precios ------------------------------------------------------------
    def _store_quote(self, q: Quote) -> None:
        market = market_of(q.symbol)
        with self.Session() as s:
            row = s.get(LatestQuote, q.symbol) or LatestQuote(symbol=q.symbol)
            row.price, row.prev_close, row.currency = q.price, q.prev_close, q.currency
            row.as_of, row.fetched_at, row.source, row.realtime = q.as_of, q.fetched_at, q.source, q.realtime
            row.status, row.error, row.error_at = "ok", None, None
            s.merge(row)
            inst = s.get(Instrument, q.symbol) or Instrument(symbol=q.symbol, market=market)
            inst.name = q.name or inst.name
            inst.currency = q.currency or inst.currency
            inst.exchange = q.exchange or inst.exchange
            inst.updated_at = utcnow()
            s.merge(inst)
            s.commit()
        if market != "FX":
            self._update_today_bar(q, market)
        self.last_refresh[q.symbol] = utcnow()

    def _update_today_bar(self, q: Quote, market: str) -> None:
        """La vela del día se arma con el precio en vivo; la historia oficial la reemplaza al cierre."""
        d = session_date(q.as_of, market)
        with self.Session() as s:
            bar = s.get(PriceBar, (q.symbol, d))
            if bar is None:
                o = q.day_open or q.price
                bar = PriceBar(symbol=q.symbol, date=d, open=o, high=max(o, q.price), low=min(o, q.price),
                               close=q.price, volume=q.volume or 0, source=q.source + LIVE_SUFFIX)
            else:
                bar.close = q.price
                bar.high = max(bar.high, q.day_high or q.price, q.price)
                bar.low = min(bar.low, q.day_low or q.price, q.price)
                if q.volume:
                    bar.volume = q.volume
            s.merge(bar)
            s.commit()

    def _store_error(self, symbol: str, err: Exception) -> None:
        with self.Session() as s:
            row = s.get(LatestQuote, symbol) or LatestQuote(symbol=symbol)
            row.status = "sin_datos" if isinstance(err, SymbolNotFound) else "error"
            row.error, row.error_at = str(err), utcnow()
            s.merge(row)
            s.commit()
        self.last_refresh[symbol] = utcnow()

    def refresh_quote(self, symbol: str) -> bool:
        try:
            self._store_quote(self.router.get_quote(symbol))
            return True
        except Exception as e:  # se guarda el error y se mantiene el último precio bueno
            log.warning("precio de %s: %s", symbol, e)
            self._store_error(symbol, e)
            return False

    def refresh_quotes(self, symbols: list[str], workers: int = 4) -> dict[str, bool]:
        if not symbols:
            return {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return dict(zip(symbols, ex.map(self.refresh_quote, symbols)))

    # --- historia -----------------------------------------------------------
    def last_bar_date(self, symbol: str) -> date | None:
        """Última vela de la historia oficial (ignora la del día armada con precios en vivo)."""
        with self.Session() as s:
            return s.scalar(
                select(func.max(PriceBar.date)).where(
                    PriceBar.symbol == symbol, PriceBar.source.not_like(f"%{LIVE_SUFFIX}")
                )
            )

    def refresh_history(self, symbol: str) -> int:
        """Descarga la historia que falta (completa la primera vez, desde una semana antes después)."""
        last = self.last_bar_date(symbol)
        if last is None:
            start = datetime.now(timezone.utc) - timedelta(days=365 * self.cfg.data.history_years + 7)
        else:
            start = datetime.combine(last - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc)
        df = self.router.get_history(symbol, start=start)
        return self._store_bars(symbol, df, source=df.attrs.get("source", "?"))

    def _store_bars(self, symbol: str, df: pd.DataFrame, source: str) -> int:
        if df.empty:
            return 0
        rows = [
            {
                "symbol": symbol,
                "date": ts.date(),
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": 0.0 if pd.isna(r.volume) else float(r.volume),
                "adj_close": None if "adj_close" not in df.columns or pd.isna(r.adj_close) else float(r.adj_close),
                "source": source,
            }
            for ts, r in df.iterrows()
        ]
        stmt = insert(PriceBar)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "date"],
            set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume", "adj_close", "source")},
        )
        with self.Session() as s:
            for i in range(0, len(rows), 500):
                s.execute(stmt, rows[i : i + 500])
            s.commit()
        return len(rows)

    def purge_placeholder_bars(self) -> int:
        """Borra velas de relleno ya guardadas (ver providers.base.drop_placeholder_bars)."""
        with self.Session() as s:
            n = s.execute(
                delete(PriceBar).where(
                    PriceBar.volume == 0, PriceBar.open == PriceBar.high, PriceBar.high == PriceBar.low,
                    PriceBar.low == PriceBar.close, PriceBar.source.not_like(f"%{LIVE_SUFFIX}"),
                    PriceBar.symbol.not_like("%=X"),
                )
            ).rowcount
            s.commit()
        if n:
            log.info("se descartaron %d velas de relleno (volumen 0, precio plano)", n)
        return n

    def bars(self, symbol: str, start: date | None = None) -> pd.DataFrame:
        with self.Session() as s:
            q = select(PriceBar).where(PriceBar.symbol == symbol)
            if start is not None:
                q = q.where(PriceBar.date >= start)
            rows = s.scalars(q.order_by(PriceBar.date)).all()
        df = pd.DataFrame(
            [(r.date, r.open, r.high, r.low, r.close, r.volume, r.adj_close) for r in rows],
            columns=["date", "open", "high", "low", "close", "volume", "adj_close"],
        )
        df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("date")), name="date")
        return df

    # --- dólar --------------------------------------------------------------
    def refresh_fx(self) -> dict | None:
        try:
            fx = self.fx.latest()
        except Exception as e:
            log.warning("dólar: %s", e)
            self._store_error(PAIR, e)
            return None
        self._store_quote(
            Quote(symbol=PAIR, price=fx.rate, prev_close=fx.prev, currency="CLP", as_of=fx.as_of,
                  source=f"{fx.source} ({fx.kind})", realtime=False, name="Dólar (CLP por USD)")
        )
        # Para la serie diaria se guarda el dato oficial; el de mercado solo si no hay uno oficial ese día.
        d = session_date(fx.as_of, "CL")
        with self.Session() as s:
            row = s.get(FxRateRow, (PAIR, d))
            if row is None or fx.kind == "observado" or row.kind != "observado":
                s.merge(FxRateRow(pair=PAIR, date=d, rate=fx.rate, source=fx.source, kind=fx.kind))
                s.commit()
        return fx.to_dict()

    def refresh_fx_history(self, years: int | None = None) -> int:
        """Serie diaria del dólar (para valorizar compras pasadas). Usa USD/CLP de Yahoo."""
        years = years or self.cfg.data.history_years
        start = datetime.now(timezone.utc) - timedelta(days=365 * years)
        closes = self.router.get_history("CLP=X", start=start)["close"]
        stmt = insert(FxRateRow).on_conflict_do_nothing(index_elements=["pair", "date"])
        rows = [{"pair": PAIR, "date": ts.date(), "rate": float(v), "source": "yfinance", "kind": "mercado"}
                for ts, v in closes.items()]
        with self.Session() as s:
            for i in range(0, len(rows), 500):
                s.execute(stmt, rows[i : i + 500])
            s.commit()
        return len(rows)

    def fx_on(self, d: date) -> float | None:
        """Dólar de una fecha (o el último disponible antes de ella)."""
        with self.Session() as s:
            return s.scalar(
                select(FxRateRow.rate).where(FxRateRow.pair == PAIR, FxRateRow.date <= d)
                .order_by(FxRateRow.date.desc()).limit(1)
            )

    # --- vistas para la API -------------------------------------------------
    def quote_view(self, symbol: str, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        market = market_of(symbol)
        with self.Session() as s:
            q = s.get(LatestQuote, symbol)
            inst = s.get(Instrument, symbol)
        as_of = as_utc(q.as_of) if q else None
        fresh = freshness(
            as_of,
            realtime=bool(q and q.realtime),
            market_open=is_open(market, now),
            now=now,
            stale_after_minutes=self.cfg.data.stale_after_minutes,
            tz=self.cfg.general.timezone,
            daily=bool(q and q.source and "observado" in q.source),
        )
        price = q.price if q else None
        prev = q.prev_close if q else None
        return {
            "symbol": symbol,
            "name": inst.name if inst else None,
            "market": market,
            "currency": (q.currency if q else None) or (inst.currency if inst else None),
            "price": price,
            "prev_close": prev,
            "change_pct": (price / prev - 1) * 100 if price and prev else None,
            "source": q.source if q else None,
            "as_of": as_of.isoformat() if as_of else None,
            "fetched_at": as_utc(q.fetched_at).isoformat() if q and q.fetched_at else None,
            "status": q.status if q else "sin_datos",
            "error": q.error if q else None,
            "freshness": fresh,
        }

    def quotes_view(self, now: datetime | None = None) -> list[dict]:
        return [self.quote_view(s, now) for s in self.watchlist()]
