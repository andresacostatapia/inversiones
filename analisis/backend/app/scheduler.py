"""Actualización periódica: precios cada `poll_seconds` con el mercado abierto, cada hora con él cerrado,
e historia diaria una vez al día por símbolo (y apenas cierra su mercado)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .market_data import MarketData
from .market_hours import is_open
from .providers.base import market_of
from .providers.fx import PAIR

log = logging.getLogger(__name__)
TICK_SECONDS = 15


def due_symbols(
    symbols: list[str],
    last_refresh: dict[str, datetime],
    now: datetime,
    poll_seconds: int,
    closed_poll_seconds: int,
    open_fn=is_open,
) -> list[str]:
    """Símbolos a los que les toca pedir precio ahora."""
    out = []
    for sym in symbols:
        last = last_refresh.get(sym)
        interval = poll_seconds if open_fn(market_of(sym), now) else closed_poll_seconds
        if last is None or (now - last).total_seconds() >= interval:
            out.append(sym)
    return out


class Poller:
    def __init__(self, md: MarketData):
        self.md = md
        self.cfg = md.cfg.data
        self.last_history: dict[str, datetime] = {}
        self.was_open: dict[str, bool] = {}
        self.last_cycle: datetime | None = None
        self.last_cycle_error: str | None = None
        self._task: asyncio.Task | None = None

    def history_due(self, sym: str, now: datetime) -> bool:
        market = market_of(sym)
        open_now = is_open(market, now)
        just_closed = self.was_open.get(market) and not open_now
        last = self.last_history.get(sym)
        return last is None or just_closed or (now - last) >= timedelta(hours=12)

    def cycle(self, now: datetime | None = None) -> dict:
        """Una pasada completa (sincrónica; se corre en un hilo aparte)."""
        now = now or datetime.now(timezone.utc)
        symbols = self.md.watchlist()
        quotes_due = due_symbols(
            symbols + [PAIR], self.md.last_refresh, now, self.cfg.poll_seconds, self.cfg.closed_poll_seconds
        )
        fx_due = PAIR in quotes_due
        quotes_due = [s for s in quotes_due if s != PAIR]
        results = self.md.refresh_quotes(quotes_due)
        if fx_due:
            self.md.refresh_fx()

        history_done = []
        for sym in symbols:
            if self.history_due(sym, now):
                try:
                    self.md.refresh_history(sym)
                    history_done.append(sym)
                except Exception as e:
                    log.warning("historia de %s: %s", sym, e)
                self.last_history[sym] = now  # también si falló: se reintenta en el próximo plazo
        for market in {market_of(s) for s in symbols}:
            self.was_open[market] = is_open(market, now)
        self.last_cycle = now
        return {"quotes": results, "fx": fx_due, "history": history_done}

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.cycle)
                self.last_cycle_error = None
            except Exception as e:  # nunca dejar morir el ciclo
                log.exception("ciclo de actualización falló")
                self.last_cycle_error = str(e)
            await asyncio.sleep(TICK_SECONDS)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
