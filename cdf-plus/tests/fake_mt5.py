"""
Simulador mínimo del paquete MetaTrader5 para probar el conector sin Windows ni terminal.
Reproduce las constantes y estructuras (namedtuples) que usa brokers.MT5Broker.
"""
from __future__ import annotations

import time
from collections import namedtuple
from typing import Any

import numpy as np

ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
SYMBOL_TRADE_MODE_DISABLED, SYMBOL_TRADE_MODE_FULL = 0, 4
TIMEFRAME_M15, TIMEFRAME_H1, TIMEFRAME_H4 = 15, 16385, 16388
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
TRADE_ACTION_DEAL = 1
ORDER_TIME_GTC = 0
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL = 10008, 10009, 10010

Account = namedtuple("Account", "login trade_mode leverage balance equity currency server trade_expert")
Terminal = namedtuple("Terminal", "trade_allowed connected")
Sym = namedtuple("Sym", "name")
SymInfo = namedtuple("SymInfo", "name digits point trade_tick_value trade_tick_size volume_min volume_max "
                                "volume_step trade_stops_level filling_mode trade_mode")
Tick = namedtuple("Tick", "time bid ask")
Check = namedtuple("Check", "retcode comment")
SendRes = namedtuple("SendRes", "retcode order deal volume price comment")
Pos = namedtuple("Pos", "ticket time type magic volume price_open sl tp profit symbol comment")
Deal = namedtuple("Deal", "profit commission swap")

# Estado configurable por los tests
STATE: dict[str, Any] = {}


def reset(trade_mode: int = ACCOUNT_TRADE_MODE_DEMO, algo: bool = True,
          symbols: tuple[str, ...] = ("XAUUSD", "XAGUSD", "US500.cash", "NAS100", "US30", "US2000.cash", "EURUSD")) -> None:
    STATE.clear()
    STATE.update(trade_mode=trade_mode, algo=algo, symbols=symbols, requests=[], positions=[], ticket=1000,
                 tick_time=int(time.time()), initialized=False, reject_check=False)


def initialize(*args: Any, **kwargs: Any) -> bool:
    STATE["initialized"] = True
    return True


def shutdown() -> None:
    STATE["initialized"] = False


def last_error() -> tuple[int, str]:
    return (1, "Success")


def account_info() -> Account:
    return Account(123456, STATE["trade_mode"], 100, 10_000.0, 10_050.0, "USD", "Broker-Demo", True)


def terminal_info() -> Terminal:
    return Terminal(STATE["algo"], True)


def symbols_get() -> tuple[Sym, ...]:
    return tuple(Sym(s) for s in STATE["symbols"])


def symbol_select(name: str, enable: bool) -> bool:
    return name in STATE["symbols"]


_SPECS = {"XAU": (2, 0.01, 1.0, 0.01), "XAG": (3, 0.001, 5.0, 0.001)}


def symbol_info(name: str) -> SymInfo:
    digits, point, tick_value, tick_size = _SPECS.get(name[:3], (1, 0.1, 0.1, 0.1))
    return SymInfo(name, digits, point, tick_value, tick_size, 0.01, 100.0, 0.01, 10, 2, SYMBOL_TRADE_MODE_FULL)


def _price(name: str) -> float:
    return {"XAU": 2650.0, "XAG": 31.0}.get(name[:3], 5000.0)


def symbol_info_tick(name: str) -> Tick:
    p = _price(name)
    return Tick(STATE["tick_time"], p - 0.1, p + 0.1)


def copy_rates_from_pos(name: str, tf: int, start: int, count: int) -> np.ndarray:
    minutes = {TIMEFRAME_M15: 15, TIMEFRAME_H1: 60, TIMEFRAME_H4: 240}[tf]
    n = min(count, 400)
    end = STATE["tick_time"] - STATE["tick_time"] % (minutes * 60)
    times = end - np.arange(n)[::-1] * minutes * 60
    rng = np.random.default_rng(len(name) + tf)
    close = _price(name) * np.exp(np.cumsum(rng.normal(0.0003, 0.002, n)))
    open_ = np.concatenate(([close[0]], close[:-1]))
    dtype = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
             ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")]
    arr = np.zeros(n, dtype=dtype)
    arr["time"], arr["open"], arr["close"] = times, open_, close
    arr["high"] = np.maximum(open_, close) * 1.001
    arr["low"] = np.minimum(open_, close) * 0.999
    arr["tick_volume"] = 10
    return arr


def order_check(req: dict[str, Any]) -> Check:
    return Check(10013 if STATE["reject_check"] else 0, "Invalid request" if STATE["reject_check"] else "Done")


def order_send(req: dict[str, Any]) -> SendRes:
    STATE["requests"].append(req)
    STATE["ticket"] += 1
    if "position" in req:
        STATE["positions"] = [p for p in STATE["positions"] if p.ticket != req["position"]]
    else:
        STATE["positions"].append(Pos(STATE["ticket"], STATE["tick_time"],
                                      POSITION_TYPE_BUY if req["type"] == ORDER_TYPE_BUY else POSITION_TYPE_SELL,
                                      req["magic"], req["volume"], req["price"], req["sl"], req["tp"], 0.0,
                                      req["symbol"], req["comment"]))
    return SendRes(TRADE_RETCODE_DONE, STATE["ticket"], STATE["ticket"], req["volume"], req["price"], "Request executed")


def positions_get(**kwargs: Any) -> tuple[Pos, ...]:
    return tuple(STATE["positions"])


def history_deals_get(**kwargs: Any) -> tuple[Deal, ...]:
    return (Deal(-12.5, -0.5, 0.0),)


reset()
