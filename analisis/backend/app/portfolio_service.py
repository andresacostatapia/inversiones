"""Cartera sobre SQLite: registro de operaciones, valorización, aportes e importación desde la PWA."""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from .db import SignalRow, Transaction, WatchlistItem
from .market_data import MarketData, normalize_symbol
from .portfolio import (
    Candidate,
    InvalidTransaction,
    PriceInfo,
    Tx,
    build_positions,
    suggest_contribution,
    value_portfolio,
)
from .providers.base import market_of, utcnow
from .providers.fx import PAIR

log = logging.getLogger(__name__)


@dataclass
class TxIn:
    symbol: str
    side: str
    quantity: float
    price: float
    date: date
    fee: float = 0.0
    fx_rate: float | None = None
    note: str | None = None


def _tx(row: Transaction) -> Tx:
    return Tx(row.symbol, row.side, row.quantity, row.price, row.currency, row.date, row.fee, row.fx_rate, row.id)


def _tx_dict(row: Transaction) -> dict:
    return {
        "id": row.id, "symbol": row.symbol, "side": row.side, "quantity": row.quantity, "price": row.price,
        "currency": row.currency, "date": row.date.isoformat(), "fee": row.fee, "fx_rate": row.fx_rate,
        "fx_source": row.fx_source, "note": row.note,
        "total": row.quantity * row.price + (row.fee if row.side == "compra" else -row.fee),
    }


def fingerprint(d: date, symbol: str, side: str, qty: float, price: float) -> str:
    return f"{d.isoformat()}|{symbol}|{side}|{qty:g}|{price:g}"


class PortfolioService:
    def __init__(self, md: MarketData, signals=None):
        self.md = md
        self.cfg = md.cfg
        self.signals = signals

    # --- operaciones --------------------------------------------------------
    def transactions(self) -> list[dict]:
        with self.md.Session() as s:
            rows = s.scalars(select(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc())).all()
        return [_tx_dict(r) for r in rows]

    def _all_tx(self) -> list[Tx]:
        with self.md.Session() as s:
            return [_tx(r) for r in s.scalars(select(Transaction)).all()]

    def _today(self) -> date:
        return datetime.now(ZoneInfo(self.cfg.general.timezone)).date()

    def _fx_for(self, d: date) -> tuple[float, str]:
        hist = self.md.fx_on(d)
        if hist:
            return hist, "historico"
        cur = self.md.quote_view(PAIR)["price"]
        if cur:
            return cur, "actual"
        raise InvalidTransaction("no hay dato del dólar para esa fecha; ingrésalo a mano (CLP por USD)")

    def _ensure_symbol(self, symbol: str) -> str:
        """El símbolo debe existir en el proveedor; si no está en la watchlist se agrega (para tener precio y señal)."""
        with self.md.Session() as s:
            in_wl = s.get(WatchlistItem, symbol) is not None
        if not in_wl:
            self.md.add_symbol(symbol)  # valida contra el proveedor; lanza SymbolNotFound
        cur = self.md.quote_view(symbol)["currency"]
        return cur or ("CLP" if market_of(symbol) == "CL" else "USD")

    def _validate(self, t: TxIn) -> TxIn:
        t.side = t.side.strip().lower()
        if t.side not in ("compra", "venta"):
            raise InvalidTransaction("el tipo debe ser 'compra' o 'venta'")
        if t.quantity <= 0 or t.price <= 0:
            raise InvalidTransaction("cantidad y precio deben ser mayores que 0")
        if t.fee < 0:
            raise InvalidTransaction("la comisión no puede ser negativa")
        if t.date > self._today():
            raise InvalidTransaction("la fecha no puede ser futura")
        if t.fx_rate is not None and t.fx_rate <= 0:
            raise InvalidTransaction("el tipo de cambio debe ser mayor que 0")
        return t

    def add_transaction(self, t: TxIn) -> dict:
        t = self._validate(t)
        symbol = normalize_symbol(t.symbol)
        # Validar primero contra lo ya registrado (sin red), para no agregar el símbolo si la operación es inválida.
        build_positions(self._all_tx() + [Tx(symbol, t.side, t.quantity, t.price, "?", t.date, t.fee, 1.0)])
        currency = self._ensure_symbol(symbol)
        fx, fx_src = (t.fx_rate, "manual") if t.fx_rate else self._fx_for(t.date)
        row = Transaction(symbol=symbol, side=t.side, quantity=t.quantity, price=t.price, currency=currency,
                          date=t.date, fee=t.fee, fx_rate=fx, fx_source=fx_src, note=t.note, created_at=utcnow())
        with self.md.Session() as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        return _tx_dict(row)

    def delete_transaction(self, tx_id: int) -> bool:
        with self.md.Session() as s:
            row = s.get(Transaction, tx_id)
            if row is None:
                return False
        rest = [t for t in self._all_tx() if t.id != tx_id]
        try:
            build_positions(rest)
        except InvalidTransaction as e:
            raise InvalidTransaction(f"no se puede borrar: una venta posterior quedaría sin respaldo ({e})")
        with self.md.Session() as s:
            s.execute(delete(Transaction).where(Transaction.id == tx_id))
            s.commit()
        return True

    # --- valorización ---------------------------------------------------------
    def fx_now(self) -> float | None:
        return self.md.quote_view(PAIR)["price"]

    def _price(self, symbol: str) -> PriceInfo:
        q = self.md.quote_view(symbol)
        return PriceInfo(q["price"], q["prev_close"], q["currency"], q["name"], q["freshness"])

    def portfolio(self) -> dict:
        rp = self.cfg.risk_profile
        positions = build_positions(self._all_tx())
        prices = {s: self._price(s) for s, p in positions.items() if p.quantity > 0}
        out = value_portfolio(positions, prices, self.fx_now(), rp.target_allocation,
                              rp.review_drawdown_pct, rp.sell_drawdown_pct)
        out["fx"] = self.md.quote_view(PAIR)
        out["monthly_contribution_clp"] = rp.monthly_contribution_clp
        return out

    def _latest_label(self, symbol: str) -> str | None:
        with self.md.Session() as s:
            row = s.scalars(select(SignalRow).where(SignalRow.symbol == symbol)
                            .order_by(SignalRow.date.desc()).limit(1)).first()
        if row is not None:
            return row.label
        if self.signals is not None:
            try:
                return self.signals.current(symbol).get("label")
            except Exception:
                return None
        return None

    def contribution(self, amount_clp: float | None = None) -> dict:
        rp = self.cfg.risk_profile
        amount = amount_clp if amount_clp is not None else rp.monthly_contribution_clp
        fx = self.fx_now()
        if not fx:
            raise ValueError("no hay dato del dólar todavía: espera la primera actualización")
        pf = self.portfolio()
        bucket_values = {a["bucket"]: a["value_clp"] for a in pf["allocation"]}
        held = {p["symbol"]: p for p in pf["positions"]}
        symbols = list(dict.fromkeys([*sorted(held), *self.md.watchlist()]))
        cands = []
        for sym in symbols:
            info = self._price(sym)
            if not info.price:
                continue
            cur = info.currency or ("CLP" if market_of(sym) == "CL" else "USD")
            price_clp = info.price * fx if cur == "USD" else info.price
            pos = held.get(sym)
            cands.append(Candidate(sym, info.price, cur, price_clp, self._latest_label(sym), pos is not None, info.name,
                                   pos["status"] if pos else None, pos["drawdown_pct"] if pos else None))
        res = suggest_contribution(amount, bucket_values, cands, rp.target_allocation, rp.fractional_shares)
        out = res.to_dict()
        out["fx"] = fx
        out["portfolio_value_clp"] = sum(bucket_values.values())
        return out

    # --- importación desde la PWA ---------------------------------------------
    def import_csv(self, text: str, commit: bool = False) -> dict:
        """Lee el CSV de la PWA (Historial → Exportar CSV) u otro con columnas parecidas.

        Columnas reconocidas: Fecha, Activo/Símbolo, Tipo (compra/venta), Cantidad, Precio, Comisión, Impuestos,
        Moneda, Tipo de cambio, Nota. Los activos en CLP sin sufijo reciben ".SN" (SQM-B → SQM-B.SN).
        Con commit=False solo muestra la vista previa; con commit=True guarda las filas listas.
        """
        rows = parse_csv(text)
        if len(rows) < 2:
            raise InvalidTransaction("el archivo no tiene filas de datos")
        cols = map_headers(rows[0])
        missing = [c for c in ("date", "symbol", "side", "quantity", "price") if c not in cols]
        if missing:
            raise InvalidTransaction(f"faltan columnas: {', '.join(missing)} (encabezados: {rows[0]})")
        existing = {fingerprint(t.date, t.symbol, t.side, t.quantity, t.price) for t in self._all_tx()}
        seen: set[str] = set()
        preview = []
        for i, r in enumerate(rows[1:], start=2):
            get = lambda k: r[cols[k]].strip() if k in cols and cols[k] < len(r) else ""  # noqa: E731
            item = {"line": i, "raw": r, "status": "lista", "error": None}
            try:
                d = parse_date(get("date"))
                side = parse_side(get("side"))
                qty, price = parse_num(get("quantity")), parse_num(get("price"))
                fee = (parse_num(get("fee")) or 0) + (parse_num(get("tax")) or 0)
                fx = parse_num(get("fx"))
                cur = get("currency").upper()
                sym = get("symbol").upper()
                if cur == "CLP" and "." not in sym:
                    sym += ".SN"
                sym = normalize_symbol(sym)
                if d is None or side is None or not qty or not price:
                    raise InvalidTransaction("fecha, tipo, cantidad o precio inválidos")
                item.update(symbol=sym, side=side, quantity=qty, price=price, date=d.isoformat(), fee=fee,
                            fx_rate=fx if cur == "USD" or fx else None, note=get("note") or None)
                fp = fingerprint(d, sym, side, qty, price)
                if fp in existing or fp in seen:
                    item["status"] = "duplicada"
                seen.add(fp)
            except (InvalidTransaction, ValueError) as e:
                item.update(status="error", error=str(e))
            preview.append(item)

        ready = [p for p in preview if p["status"] == "lista"]
        # Validar que las ventas tengan respaldo considerando lo ya registrado + lo nuevo.
        try:
            build_positions(self._all_tx() + [
                Tx(p["symbol"], p["side"], p["quantity"], p["price"], "?", date.fromisoformat(p["date"]), p["fee"], 1.0)
                for p in ready
            ])
            consistency = None
        except InvalidTransaction as e:
            consistency = str(e)
        result = {"rows": preview, "ready": len(ready), "duplicates": sum(p["status"] == "duplicada" for p in preview),
                  "errors": sum(p["status"] == "error" for p in preview), "consistency_error": consistency,
                  "imported": 0}
        if commit:
            if consistency:
                raise InvalidTransaction(f"no se importó nada: {consistency}")
            for p in sorted(ready, key=lambda p: (p["date"], p["side"] != "compra")):
                try:
                    self.add_transaction(TxIn(p["symbol"], p["side"], p["quantity"], p["price"],
                                              date.fromisoformat(p["date"]), p["fee"], p["fx_rate"], p["note"]))
                    p["status"] = "importada"
                    result["imported"] += 1
                except Exception as e:  # p. ej. símbolo que el proveedor no conoce
                    p["status"], p["error"] = "error", str(e)
        return result


# --- utilidades de CSV ------------------------------------------------------------

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()
    return re.sub(r"\s+", " ", s)


HEADERS = {
    "date": ("fecha", "date"),
    "symbol": ("activo", "simbolo", "symbol", "ticker", "instrumento"),
    "side": ("tipo", "side", "operacion", "tipo de operacion"),
    "quantity": ("cantidad", "quantity", "unidades", "acciones"),
    "price": ("precio", "price", "precio unitario"),
    "fee": ("comision", "fee", "comisiones"),
    "tax": ("impuestos", "tax", "impuesto"),
    "currency": ("moneda", "currency"),
    "fx": ("tipo de cambio", "fx", "fx rate", "dolar"),
    "note": ("nota", "note", "notas", "comentario"),
}


def map_headers(header: list[str]) -> dict[str, int]:
    out = {}
    for i, h in enumerate(header):
        n = _norm(h)
        for key, names in HEADERS.items():
            if n in names and key not in out:
                out[key] = i
    return out


def parse_csv(text: str) -> list[list[str]]:
    text = text.lstrip("﻿")
    first = text.splitlines()[0] if text else ""
    delim = max([";", "\t", ","], key=lambda d: first.count(d))
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]


def parse_num(raw: str) -> float | None:
    """Acepta "1.234,56", "1,234.56", "262,10", "6.850" (miles chilenos) y "1234.5"."""
    s = re.sub(r"[^\d.,-]", "", raw or "")
    if not re.search(r"\d", s):
        return None
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        s = s.replace(".", "").replace(",", ".") if last_comma > last_dot else s.replace(",", "")
    elif last_comma >= 0:
        s = s.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(raw: str) -> date | None:
    s = (raw or "").strip()[:10]
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    return None


def parse_side(raw: str) -> str | None:
    n = _norm(raw)
    if n in ("compra", "buy", "c", "b", "comprar"):
        return "compra"
    if n in ("venta", "sell", "v", "s", "vender"):
        return "venta"
    return None

