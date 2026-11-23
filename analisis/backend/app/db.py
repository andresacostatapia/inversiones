"""SQLite con SQLAlchemy 2.0. Las tablas se crean al iniciar (sin migraciones en la v1)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    __tablename__ = "instruments"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))
    market: Mapped[str] = mapped_column(String(4))  # CL / US / FX
    currency: Mapped[str | None] = mapped_column(String(8))
    exchange: Mapped[str | None] = mapped_column(String(60))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PriceBar(Base):
    """Vela diaria. La del día en curso se actualiza con cada precio nuevo hasta el cierre."""

    __tablename__ = "price_bars"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0)
    adj_close: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20))


class LatestQuote(Base):
    """Último precio conocido por símbolo, con su origen, hora y estado."""

    __tablename__ = "quotes"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    price: Mapped[float | None] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(20))
    realtime: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok / error / sin_datos
    error: Mapped[str | None] = mapped_column(Text)
    error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FxRateRow(Base):
    """Tipo de cambio diario (CLP por USD). Sirve para valorizar compras pasadas con el dólar de su fecha."""

    __tablename__ = "fx_rates"
    pair: Mapped[str] = mapped_column(String(10), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    rate: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(12))  # observado / mercado


class SignalRow(Base):
    """Señal por símbolo y día. La del día en curso se sobrescribe hasta el cierre (provisional)."""

    __tablename__ = "signals"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    label: Mapped[str | None] = mapped_column(String(10))
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    provisional: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text)  # SignalResult completo en JSON


def make_engine(path: Path | str):
    url = "sqlite://" if str(path) == ":memory:" else f"sqlite:///{path}"
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"connect_args": {"check_same_thread": False}}
    if str(path) == ":memory:":
        from sqlalchemy.pool import StaticPool

        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _):  # WAL: la API puede leer mientras el refresco escribe
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
