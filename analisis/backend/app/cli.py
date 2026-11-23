"""Línea de comandos para probar la capa de datos sin levantar la API.

    python -m app.cli quote SQM-B.SN SPY
    python -m app.cli indicators VOO
    python -m app.cli fx
    python -m app.cli refresh        # una pasada completa que guarda en SQLite
    python -m app.cli status
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from . import indicators as ind
from .config import get_config, get_secrets
from .market_hours import freshness, is_open, session_info
from .providers.base import ProviderError, market_of
from .providers.fx import build_fx_service
from .providers.router import build_router

DISCLAIMER = "⚠  Herramienta educativa, no es asesoría financiera."


def _fmt(v, nd=2, suffix=""):
    if v is None:
        return "—"
    return f"{v:,.{nd}f}{suffix}".replace(",", "_").replace(".", ",").replace("_", ".")


def cmd_quote(args, cfg, router) -> int:
    rc = 0
    now = datetime.now(timezone.utc)
    for raw in args.symbols:
        sym = raw.upper()
        try:
            q = router.get_quote(sym)
        except ProviderError as e:
            print(f"{sym:<10} ERROR: {e}")
            rc = 1
            continue
        f = freshness(q.as_of, realtime=q.realtime, market_open=is_open(market_of(sym), now), now=now,
                      stale_after_minutes=cfg.data.stale_after_minutes, tz=cfg.general.timezone)
        chg = q.change_pct
        print(f"{sym:<10} {_fmt(q.price):>14} {q.currency or '':<4} {_fmt(chg, 2, ' %') if chg is not None else '—':>9}"
              f"   [{q.source}] {f['label']}")
        if q.name:
            print(f"{'':<10} {q.name} · {q.exchange or ''}")
    return rc


def cmd_indicators(args, cfg, router) -> int:
    sym = args.symbol.upper()
    start = datetime.now(timezone.utc) - timedelta(days=int(args.years * 365.25) + 10)
    try:
        bars = router.get_history(sym, start=start)
    except ProviderError as e:
        print(f"ERROR: {e}")
        return 1
    df = ind.compute_all(bars)
    snap = ind.latest_snapshot(df)
    print(f"{sym} — {len(df)} velas diarias, última {df.index[-1].date()} [{bars.attrs.get('source')}]\n")
    rows = [
        ("Cierre", snap["close"], ""), ("SMA 20", snap["sma20"], ""), ("SMA 50", snap["sma50"], ""),
        ("SMA 200", snap["sma200"], ""), ("EMA 20", snap["ema20"], ""), ("EMA 50", snap["ema50"], ""),
        ("EMA 200", snap["ema200"], ""), ("RSI 14", snap["rsi14"], ""), ("MACD", snap["macd"], ""),
        ("MACD señal", snap["macd_signal"], ""), ("MACD histograma", snap["macd_hist"], ""),
        ("Bollinger sup.", snap["bb_upper"], ""), ("Bollinger inf.", snap["bb_lower"], ""),
        ("Bollinger %B", snap["bb_pctb"], ""), ("ATR 14", snap["atr14"], ""), ("ATR 14 (% precio)", snap["atr14_pct"], " %"),
        ("Volumen relativo", snap["rel_volume"], " x"),
    ]
    for label, v, suf in rows:
        print(f"  {label:<20}{_fmt(v, 2, suf):>16}")
    print()
    m = ind.basic_metrics(df)
    labels = {
        "return_1m_pct": "Retorno 1 mes", "return_3m_pct": "Retorno 3 meses", "return_6m_pct": "Retorno 6 meses",
        "return_1y_pct": "Retorno 1 año", "volatility_1y_pct": "Volatilidad anual", "high_52w": "Máximo 52 sem.",
        "low_52w": "Mínimo 52 sem.", "from_high_52w_pct": "Desde el máximo", "dist_sma200_pct": "Distancia a SMA200",
    }
    for k, label in labels.items():
        suf = "" if k in ("high_52w", "low_52w") else " %"
        print(f"  {label:<20}{_fmt(m.get(k), 2, suf):>16}")
    return 0


def cmd_fx(args, cfg, router) -> int:
    svc = build_fx_service(cfg.data.fx.sources, router.providers.get("yfinance"))
    try:
        fx = svc.latest()
    except ProviderError as e:
        print(f"ERROR: {e}")
        return 1
    for name, err in svc.last_errors.items():
        print(f"  (fuente {name} falló: {err})")
    print(f"USD/CLP {_fmt(fx.rate)}  [{fx.source}, {fx.kind}]  fecha {fx.as_of.isoformat()}")
    return 0


def cmd_refresh(args, cfg, router) -> int:
    from .db import make_engine, make_session_factory
    from .market_data import MarketData
    from .scheduler import Poller

    md = MarketData(cfg, make_session_factory(make_engine(cfg.database_path)), router,
                    build_fx_service(cfg.data.fx.sources, router.providers.get("yfinance")))
    md.seed_watchlist()
    res = Poller(md).cycle()
    print(f"Base de datos: {cfg.database_path}")
    for sym, ok in res["quotes"].items():
        print(f"  precio   {sym:<10} {'ok' if ok else 'ERROR'}")
    print(f"  dólar    {'ok' if res['fx'] else '—'}")
    for sym in res["history"]:
        print(f"  historia {sym:<10} {len(md.bars(sym))} velas")
    for v in md.quotes_view():
        print(f"  {v['symbol']:<10} {_fmt(v['price']):>14} {v['currency'] or '':<4} {v['freshness']['label']}"
              + (f"  ⚠ {v['error']}" if v["status"] != "ok" else ""))
    return 0 if all(res["quotes"].values()) else 1


def cmd_status(args, cfg, router) -> int:
    for m in ("CL", "US", "FX"):
        s = session_info(m)
        extra = f"cierra {s['closes_at']}" if s.get("closes_at") else (f"abre {s['next_open']}" if s.get("next_open") else "")
        print(f"  {s['name']:<18} {'ABIERTO' if s['open'] else 'cerrado':<8} {extra}")
    print()
    for p in router.status():
        print(f"  {p['name']:<13} {'configurado' if p['configured'] else 'sin API key':<12} mercados {','.join(p['markets'])}"
              f"  tiempo real: {','.join(p['realtime_markets']) or 'no'}  historia: {'sí' if p['history'] else 'no'}")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=DISCLAIMER)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("quote", help="precio actual de uno o más símbolos")
    p.add_argument("symbols", nargs="+")
    p = sub.add_parser("indicators", help="indicadores técnicos y métricas de un símbolo")
    p.add_argument("symbol")
    p.add_argument("--years", type=float, default=2.0, help="años de historia a descargar (mín. ~1 para SMA200)")
    sub.add_parser("fx", help="dólar observado / USD-CLP")
    sub.add_parser("refresh", help="actualiza la watchlist y guarda en SQLite")
    sub.add_parser("status", help="mercados y proveedores")
    args = parser.parse_args(argv)

    cfg = get_config()
    router = build_router(cfg, get_secrets())
    print(DISCLAIMER + "\n")
    cmds = {"quote": cmd_quote, "indicators": cmd_indicators, "fx": cmd_fx, "refresh": cmd_refresh, "status": cmd_status}
    return cmds[args.cmd](args, cfg, router)


if __name__ == "__main__":
    sys.exit(main())
