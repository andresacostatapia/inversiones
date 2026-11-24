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
    kind: Mapped[str | None] = mapped_column(String(20))  # EQUITY, ETF, INDEX, FUTURE, CURRENCY, CRYPTOCURRENCY…
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


class Transaction(Base):
    """Operación registrada a mano (o importada). Las posiciones se calculan desde aquí."""

    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    side: Mapped[str] = mapped_column(String(6))          # compra / venta
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)            # en la moneda del instrumento
    currency: Mapped[str] = mapped_column(String(8))
    date: Mapped[date] = mapped_column(Date)
    fee: Mapped[float] = mapped_column(Float, default=0)   # comisión + impuestos, en la misma moneda
    fx_rate: Mapped[float] = mapped_column(Float)          # CLP por 1 USD en la fecha de la operación
    fx_source: Mapped[str] = mapped_column(String(20))     # manual / historico / actual
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AlertRow(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(120), index=True)   # evita repetir la misma alerta
    kind: Mapped[str] = mapped_column(String(20))       # revision / venta / senal / datos / prueba
    severity: Mapped[str] = mapped_column(String(10))   # info / warning / critical
    symbol: Mapped[str | None] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    value: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # la condición dejó de cumplirse
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified: Mapped[str | None] = mapped_column(String(60))     # canales externos que la recibieron
    notify_error: Mapped[str | None] = mapped_column(Text)


class CfdState(Base):
    """Estado del copiloto CFD: ajustes ("settings") y calibraciones ("calibration:GOLD"…), en JSON."""

    __tablename__ = "cfd_state"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
    _migrate(engine)
    return engine


# Columnas agregadas después de la primera versión: create_all no modifica tablas existentes.
ADDED_COLUMNS = {"instruments": {"kind": "VARCHAR(20)"}}


def _migrate(engine) -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in ADDED_COLUMNS.items():
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def make_session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
