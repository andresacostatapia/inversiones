"""Calcula señales desde la historia guardada, las persiste y corre backtests."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from . import indicators as ind
from .backtest import run_backtest
from .config import SignalsConfig
from .db import SignalRow
from .market_data import MarketData
from .providers.base import utcnow
from .signals import evaluate, score_frame, score_history

log = logging.getLogger(__name__)


class NoHistory(LookupError):
    pass


class SignalService:
    def __init__(self, md: MarketData):
        self.md = md
        self.cfg = md.cfg

    def _bars(self, symbol: str) -> pd.DataFrame:
        bars = self.md.bars(symbol)
        if bars.empty:
            raise NoHistory(f"Sin historia guardada para {symbol}")
        return bars

    def current(self, symbol: str, store: bool = True) -> dict:
        bars = self._bars(symbol)
        quote = self.md.quote_view(symbol)
        kind = quote["freshness"]["kind"]
        df = ind.compute_all(bars)
        provisional = kind in ("tiempo_real", "retraso", "desactualizado") and \
            df.index[-1].date() >= _session_date_of(quote)
        res = evaluate(symbol, df, self.cfg.signals, provisional=provisional, stale=kind == "desactualizado",
                       frame=score_frame(df, self.cfg.signals, provisional_last=provisional))
        out = res.to_dict()
        out["price"] = {k: quote[k] for k in ("price", "currency", "change_pct", "freshness")}
        out["previous"] = self._previous(symbol, res.date)
        out["changed"] = bool(out["previous"] and res.label and out["previous"]["label"] != res.label)
        if store and res.label:
            self._store(res, out)
        return out

    def _previous(self, symbol: str, day: str) -> dict | None:
        """Última señal guardada de un día anterior (para mostrar si la señal cambió)."""
        with self.md.Session() as s:
            row = s.scalars(
                select(SignalRow).where(SignalRow.symbol == symbol, SignalRow.date < date.fromisoformat(day))
                .order_by(SignalRow.date.desc()).limit(1)
            ).first()
        if row is None:
            return None
        return {"date": row.date.isoformat(), "label": row.label, "score": row.score}

    def _store(self, res, payload: dict) -> None:
        values = {
            "symbol": res.symbol, "date": date.fromisoformat(res.date), "computed_at": utcnow(),
            "label": res.label, "score": res.score, "confidence": res.confidence, "provisional": res.provisional,
            "summary": res.summary, "payload": json.dumps(payload, ensure_ascii=False, default=str),
        }
        stmt = insert(SignalRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=["symbol", "date"],
                                          set_={k: v for k, v in values.items() if k not in ("symbol", "date")})
        with self.md.Session() as s:
            s.execute(stmt)
            s.commit()

    def all_current(self) -> list[dict]:
        out = []
        for sym in self.md.watchlist():
            try:
                out.append(self.current(sym))
            except NoHistory as e:
                out.append({"symbol": sym, "label": None, "summary": str(e), "reasons": [], "strategies": []})
            except Exception as e:  # una serie con problemas no debe romper la lista completa
                log.exception("señal de %s", sym)
                out.append({"symbol": sym, "label": None, "summary": f"Error al calcular la señal: {e}",
                            "reasons": [], "strategies": []})
        return out

    def history(self, symbol: str, days: int | None = 365) -> list[dict]:
        h = score_history(self._bars(symbol), self.cfg.signals)
        if days:
            h = h[h.index >= h.index[-1] - timedelta(days=days)]
        h = h.astype(object).where(h.notna(), None)
        return [{"date": d.date().isoformat(), **{k: (round(v, 1) if isinstance(v, float) else v)
                                                  for k, v in r.items()}} for d, r in h.iterrows()]

    def backtest(self, symbol: str, years: float | None = None, strategies: list[str] | None = None,
                 commission_pct: float | None = None, slippage_pct: float | None = None,
                 thresholds: dict | None = None, weights: dict | None = None) -> dict:
        bt = self.cfg.backtest.model_copy(update={k: v for k, v in
                                                  {"commission_pct": commission_pct, "slippage_pct": slippage_pct}.items()
                                                  if v is not None})
        sig: SignalsConfig = self.cfg.signals
        if thresholds or weights:
            sig = sig.model_copy(deep=True)
            if thresholds:
                sig.thresholds = sig.thresholds.model_copy(update=thresholds)
            if weights:
                sig.weights = {**sig.weights, **weights}
        return run_backtest(symbol, self._bars(symbol), sig, bt, years=years or self.cfg.backtest.default_years,
                            strategies=strategies)


def _session_date_of(quote: dict) -> date:
    """Fecha local del último precio (aprox. la sesión en curso)."""
    loc = quote["freshness"].get("as_of_local")
    return date.fromisoformat(loc[:10]) if loc else date.min

