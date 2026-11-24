"""
bot.py — Bot de ejecución automática de CDF Plus (papel · demo MT5 · real MT5).

Uso:
    python bot.py               # Bucle continuo (Ctrl + C para detener)
    python bot.py --once        # Un solo ciclo (útil para probar la configuración)
    python bot.py --reset-paper # Reinicia la cuenta simulada del modo papel

Configuración en .env (ver .env.example): BOT_MODE=paper|demo|real y el resto de BOT_*.
El panel "Bot MT5" de la app muestra el estado y permite pausar o cerrar todo.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import config
import data_fetcher as dfetch
import indicators as ind
import notifier as notif
from bot_engine import (DayState, Decision, append_log, heartbeat_age_s, load_bot_config, plan_entries,
                        read_control, read_state, write_control, write_json)
from brokers import Broker, BrokerError, MT5Broker, PaperBroker, Position, make_broker
from config import BotConfig

logger = logging.getLogger("cdfplus.bot")


class Bot:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self.broker: Broker | None = None
        self.source = "feed"
        self.day = DayState()
        self.notifier = notif.TelegramNotifier()
        self.events: list[dict[str, Any]] = []
        self.last_error = ""
        self.known_tickets: dict[int, Position] = {}
        self.last_skip: dict[str, str] = {}
        self.signals_summary: list[dict[str, Any]] = []
        self.regime = ""
        self.running = True
        self.started_at = datetime.now(timezone.utc).isoformat()

    # --- utilidades -------------------------------------------------------------
    def event(self, kind: str, message: str, notify: bool = False, **extra: Any) -> None:
        entry = {"kind": kind, "message": message, "mode": self.cfg.mode, **extra}
        append_log(entry)
        self.events.insert(0, {"time": datetime.now(timezone.utc).isoformat(), **entry})
        del self.events[40:]
        level = logging.WARNING if kind in ("ERROR", "HALT", "EMERGENCY") else logging.INFO
        logger.log(level, "%s · %s", kind, message)
        if notify and self.cfg.notify_telegram and self.notifier.is_configured:
            res = self.notifier.send(f"🤖 <b>{config.APP_NAME} · bot {self.cfg.mode.upper()}</b>\n{message}")
            if not res.ok:
                logger.warning("Telegram: %s", res.message)

    def connect(self) -> None:
        self.broker, self.source = make_broker(self.cfg)
        acc = self.broker.account()
        extra = ""
        if isinstance(self.broker, MT5Broker):
            extra = f" · símbolos {self.broker.symbols} · sin símbolo: {self.broker.missing or 'ninguno'}"
        elif isinstance(self.broker, PaperBroker) and self.broker.price_source is not None:
            extra = f" · precios MT5 {self.broker.price_source.symbols}"
        self.event("START", f"Bot iniciado en modo {self.cfg.mode.upper()} ({self.broker.name}) · datos {self.source} "
                            f"· equity {acc.equity:,.2f} {acc.currency}{extra}", notify=True)

    def snapshot(self) -> tuple[dfetch.MarketSnapshot, Any]:
        """Datos completos (con vela en curso) y la hora de referencia para descartarla."""
        assert self.broker is not None
        if self.source == "mt5":
            mt5b = self.broker if isinstance(self.broker, MT5Broker) else self.broker.price_source  # type: ignore[union-attr]
            assert mt5b is not None
            return mt5b.fetch_snapshot(), mt5b.server_time()
        return dfetch.fetch_snapshot_uncached(dfetch.PROVIDER_AUTO), None

    # --- ciclo -----------------------------------------------------------------
    def emergency_close(self) -> None:
        assert self.broker is not None
        positions = self.broker.positions()
        results = []
        for p in positions:
            res = self.broker.close_position(p)
            results.append(f"{p.key} #{p.ticket}: {'OK' if res.ok else 'FALLÓ'} {res.message}")
        write_control(emergency_close=False, paused=True)
        self.event("EMERGENCY", "PARADA DE EMERGENCIA: posiciones cerradas y bot en pausa. "
                                + ("; ".join(results) if results else "No había posiciones abiertas."), notify=True)

    def track_closed(self, current: list[Position], paper_closed: list[dict[str, Any]]) -> None:
        for rec in paper_closed:
            self.event("CLOSE", f"{rec['key']} #{rec['ticket']} cerrada por {rec['reason']} a {rec['close_price']:.5g} "
                                f"· P/L {rec['pnl']:+,.2f}", notify=True, key=rec["key"], pnl=rec["pnl"])
        current_ids = {p.ticket for p in current}
        if isinstance(self.broker, MT5Broker):
            for ticket, pos in list(self.known_tickets.items()):
                if ticket not in current_ids:
                    pnl = self.broker.closed_profit(ticket)
                    pnl_txt = f"{pnl:+,.2f}" if pnl is not None else "n/d"
                    self.event("CLOSE", f"{pos.key} #{ticket} cerrada en el servidor (SL/TP/manual) · P/L {pnl_txt}",
                               notify=True, key=pos.key, pnl=pnl)
        self.known_tickets = {p.ticket: p for p in current}

    def execute(self, decisions: list[Decision]) -> None:
        assert self.broker is not None
        for d in decisions:
            if d.action == "SKIP":
                tag = f"{d.reason}|{d.bar_time}"
                if self.last_skip.get(d.key) != tag:  # No repetir el mismo descarte en cada ciclo
                    self.last_skip[d.key] = tag
                    self.event("SKIP", f"{d.key} {d.score:+.0f}: {d.reason}", key=d.key, score=d.score)
                continue
            side = "COMPRA" if d.direction > 0 else "VENTA"
            res = self.broker.market_order(d.key, d.direction, d.lots, d.sl, d.tp, comment=f"CDFPlus {d.score:+.0f}")
            px = f"{res.price:.{d.digits}f}" if res.price is not None else "mercado"
            if res.ok:
                self.day.trades_today += 1
                self.day.traded_bars[d.key] = d.bar_time
                self.event("OPEN", f"{side} {d.key} ({d.symbol}) {d.lots:.2f} lotes @ {px} · SL {d.sl:.{d.digits}f} · TP {d.tp:.{d.digits}f} "
                                   f"· riesgo {d.risk_amount:,.2f} · {d.reason}", notify=True,
                           key=d.key, ticket=res.ticket, lots=d.lots, sl=d.sl, tp=d.tp, score=d.score)
            else:
                # Se marca la vela para no reintentar una orden rechazada en bucle
                self.day.traded_bars[d.key] = d.bar_time
                self.event("ERROR", f"No se pudo abrir {side} {d.key}: {res.message}", notify=True, key=d.key)

    def cycle(self) -> None:
        assert self.broker is not None
        control = read_control()
        if control.get("emergency_close"):
            self.emergency_close()
            control = read_control()

        full, ref_time = self.snapshot()
        paper_closed = self.broker.update(full)
        closed_snap = dfetch.drop_incomplete_bars(full, ref_time)
        signals, ctx = ind.analyze_market(closed_snap)
        self.regime = ctx.regime_label
        account = self.broker.account()
        positions = self.broker.positions()
        self.track_closed(positions, paper_closed)

        today = datetime.now(timezone.utc).date().isoformat()
        if self.day.roll(account.equity, today):
            self.event("DAY", f"Nuevo día {today}: equity inicial {account.equity:,.2f} {account.currency}")
            self.last_skip.clear()

        self.signals_summary = [
            {"key": k, "score": round(s.score, 1), "verdict": s.verdict, "ok": s.ok,
             "bar": s.signal_bar_time().isoformat() if s.ok and s.signal_bar_time() is not None else "",
             "stale": closed_snap.assets[k].is_stale}
            for k, s in signals.items()
        ]

        if control.get("paused"):
            decisions: list[Decision] = []
        else:
            spec_fn = self.broker.symbol_spec
            decisions = plan_entries(signals, closed_snap, positions, account, spec_fn, self.cfg, self.day)
            was_halted = bool(self.day.halted_reason)
            self.execute(decisions)
            if self.day.halted_reason and not was_halted:
                self.event("HALT", self.day.halted_reason, notify=True)

        self.write_state(account, self.broker.positions(), paused=bool(control.get("paused")))
        self.last_error = ""

    def write_state(self, account: Any, positions: list[Position], paused: bool) -> None:
        assert self.broker is not None
        state = {
            "heartbeat": datetime.now(timezone.utc).isoformat(),
            "stopped": False,
            "started_at": self.started_at,
            "pid": os.getpid(),
            "mode": self.cfg.mode,
            "broker": self.broker.name,
            "data_source": self.source,
            "paused": paused,
            "config": asdict(self.cfg),
            "account": asdict(account),
            "day": asdict(self.day),
            "daily_pnl_pct": self.day.daily_pnl_pct(account.equity),
            "positions": [asdict(p) for p in positions],
            "signals": self.signals_summary,
            "regime": self.regime,
            "events": self.events,
            "last_error": self.last_error,
            "symbols": getattr(self.broker, "symbols", {}) or getattr(getattr(self.broker, "price_source", None),
                                                                     "symbols", {}),
        }
        write_json(config.BOT_STATE_FILE, state)

    # --- bucle -------------------------------------------------------------------
    def run(self, once: bool = False) -> None:
        self.connect()
        failures = 0
        while self.running:
            try:
                self.cycle()
                failures = 0
            except BrokerError as exc:
                failures += 1
                self.last_error = str(exc)
                self.event("ERROR", f"Bróker: {exc}", notify=failures == 1)
                self._try_reconnect()
            except Exception as exc:  # El bot nunca debe morir por un error puntual
                failures += 1
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                self.event("ERROR", f"Error en el ciclo: {self.last_error}", notify=failures == 1)
                logger.exception("Error en el ciclo")
            if once:
                break
            wait = min(self.cfg.loop_seconds * (2 ** min(failures, 4)), 600) if failures else self.cfg.loop_seconds
            self._sleep(wait)
        self.shutdown()

    def _sleep(self, seconds: float) -> None:
        """Espera revisando el archivo de control cada segundo (reacción rápida a la emergencia)."""
        end = time.time() + seconds
        while self.running and time.time() < end:
            if read_control().get("emergency_close"):
                return
            time.sleep(1)

    def _try_reconnect(self) -> None:
        try:
            if self.broker is not None:
                self.broker.shutdown()
            self.broker, self.source = make_broker(self.cfg)
            self.event("RECONNECT", "Reconectado con el bróker")
        except Exception as exc:
            logger.warning("Reconexión fallida: %s", exc)

    def shutdown(self) -> None:
        if self.broker is not None:
            try:
                acc = self.broker.account()
                self.write_state(acc, self.broker.positions(), paused=bool(read_control().get("paused")))
            except Exception:
                pass
            self.broker.shutdown()
        final = read_state()
        if final:
            final["stopped"] = True
            write_json(config.BOT_STATE_FILE, final)
        if self.cfg.mode == "paper":
            msg = "Bot detenido. Modo papel: las posiciones simuladas se revisan al volver a arrancarlo."
        else:
            msg = "Bot detenido. Las posiciones abiertas mantienen su SL/TP en el servidor del bróker."
        self.event("STOP", msg, notify=True)


def setup_logging() -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s · %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout),
                                       logging.FileHandler(config.STATE_DIR / "bot.log", encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)
    for noisy in ("yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bot de ejecución automática de CDF Plus")
    parser.add_argument("--once", action="store_true", help="Ejecuta un solo ciclo y termina")
    parser.add_argument("--reset-paper", action="store_true", help="Reinicia la cuenta simulada")
    parser.add_argument("--force", action="store_true", help="Arranca aunque parezca haber otro bot activo")
    args = parser.parse_args(argv)
    setup_logging()

    try:
        cfg = load_bot_config()
    except ValueError as exc:
        logger.error("Configuración inválida: %s", exc)
        return 2

    if args.reset_paper:
        PaperBroker(cfg.paper_balance).reset()
        logger.info("Cuenta de papel reiniciada con %.2f", cfg.paper_balance)
        return 0

    age = heartbeat_age_s(read_state())
    if age is not None and age < max(90, cfg.loop_seconds * 3) and not args.force:
        logger.error("Parece que ya hay un bot en marcha (latido hace %.0f s). Usa --force si no es así.", age)
        return 3

    bot = Bot(cfg)

    def _stop(*_: Any) -> None:
        bot.running = False

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    try:
        bot.run(once=args.once)
    except BrokerError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
