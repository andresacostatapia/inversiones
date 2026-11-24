"""Tipo de cambio USD/CLP.

- mindicador.cl: dólar observado del Banco Central (uno por día hábil). Es el oficial para valorizar.
- yfinance CLP=X: precio de mercado durante el día (respaldo, y para ver el movimiento intradía).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import httpx
import pandas as pd

from .base import ProviderError, utcnow
from .yfinance_provider import YFinanceProvider

PAIR = "USDCLP"


@dataclass
class FxRate:
    pair: str
    rate: float              # CLP por 1 USD
    as_of: datetime
    source: str
    kind: str                # "observado" (Banco Central) o "mercado"
    prev: float | None = None
    fetched_at: datetime | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        d["fetched_at"] = (self.fetched_at or utcnow()).isoformat()
        return d


class MindicadorFx:
    name = "mindicador"
    base_url = "https://mindicador.cl/api"

    def __init__(self, client: httpx.Client | None = None, timeout: float = 10.0):
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout)

    def _serie(self, path: str) -> list[dict]:
        try:
            r = self._client.get(path)
            r.raise_for_status()
            serie = r.json().get("serie") or []
        except (httpx.HTTPError, ValueError) as e:
            raise ProviderError(f"mindicador: {e.__class__.__name__}") from e
        if not serie:
            raise ProviderError("mindicador: sin datos del dólar")
        return serie

    def latest(self) -> FxRate:
        serie = self._serie("/dolar")
        cur = serie[0]
        return FxRate(
            pair=PAIR,
            rate=float(cur["valor"]),
            as_of=datetime.fromisoformat(cur["fecha"].replace("Z", "+00:00")),
            source=self.name,
            kind="observado",
            prev=float(serie[1]["valor"]) if len(serie) > 1 else None,
            fetched_at=utcnow(),
        )

    def history(self, year: int) -> pd.Series:
        """Dólar observado de un año completo (índice: fecha)."""
        serie = self._serie(f"/dolar/{year}")
        s = pd.Series(
            {pd.Timestamp(x["fecha"]).tz_localize(None).normalize(): float(x["valor"]) for x in serie}, dtype=float
        )
        return s.sort_index()


class YahooFx:
    name = "yfinance"
    symbol = "CLP=X"

    def __init__(self, provider: YFinanceProvider | None = None):
        self._p = provider or YFinanceProvider()

    def latest(self) -> FxRate:
        q = self._p.get_quote(self.symbol)
        return FxRate(pair=PAIR, rate=q.price, as_of=q.as_of, source=self.name, kind="mercado",
                      prev=q.prev_close, fetched_at=q.fetched_at)

    def history(self, start: datetime | None = None) -> pd.Series:
        return self._p.get_history(self.symbol, start=start)["close"]


class FxService:
    """Prueba las fuentes en orden y devuelve la primera que responda."""

    def __init__(self, sources: list):
        self.sources = sources
        self.last_errors: dict[str, str] = {}

    def latest(self) -> FxRate:
        errors = []
        for src in self.sources:
            try:
                fx = src.latest()
                self.last_errors.pop(src.name, None)
                return fx
            except Exception as e:  # una fuente caída no debe tumbar la app
                self.last_errors[src.name] = str(e)
                errors.append(f"{src.name}: {e}")
        raise ProviderError("no se pudo obtener el dólar — " + "; ".join(errors))


def build_fx_service(source_names: list[str], yf: YFinanceProvider | None = None) -> FxService:
    makers = {"mindicador": MindicadorFx, "yfinance": lambda: YahooFx(yf)}
    return FxService([makers[n]() for n in source_names if n in makers])

