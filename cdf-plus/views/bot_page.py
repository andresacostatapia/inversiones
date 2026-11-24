"""Página del Bot MT5: estado en vivo, posiciones, decisiones y controles de seguridad."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

import config
from bot_engine import heartbeat_age_s, read_control, read_log, read_state, write_control
from brokers import mt5_available

st.markdown("<div class='cdf-header'><span class='cdf-title'>Bot MT5</span>"
            "<span class='cdf-sub'>Ejecución automática · papel → demo → real</span></div>",
            unsafe_allow_html=True)

MODE_LABEL = {"paper": "PAPEL (simulado)", "demo": "DEMO (MT5)", "real": "REAL (MT5)"}


@st.fragment(run_every=10)
def live_panel() -> None:
    state = read_state()
    control = read_control()
    age = heartbeat_age_s(state)
    loop_s = int(state.get("config", {}).get("loop_seconds", 30)) if state else 30
    running = not state.get("stopped") and age is not None and age < max(90, loop_s * 3)

    if not state:
        st.info("El bot aún no se ha ejecutado. Arráncalo con **iniciar_bot.bat** (o `python bot.py`).")
        return

    mode = state.get("mode", "?")
    acc = state.get("account", {})
    day = state.get("day", {})
    status = ("🟢 EN MARCHA" if running else "🔴 DETENIDO") + (" · ⏸ EN PAUSA" if control.get("paused") else "")
    cls = "up" if running and not control.get("paused") else "warnc" if running else "down"
    mode_cls = "down" if mode == "real" else "warnc" if mode == "demo" else "muted"
    pills = [
        f"<div class='cdf-pill cdf-regime'>Estado <b class='{cls}'>{status}</b></div>",
        f"<div class='cdf-pill'>Modo <b class='{mode_cls}'>{MODE_LABEL.get(mode, mode)}</b></div>",
        f"<div class='cdf-pill'>Bróker <b>{html.escape(str(state.get('broker', '')))}</b></div>",
        f"<div class='cdf-pill'>Datos <b>{html.escape(str(state.get('data_source', '')))}</b></div>",
        f"<div class='cdf-pill'>Último latido <b>{'hace ' + str(int(age)) + ' s' if age is not None else '—'}</b></div>",
        f"<div class='cdf-pill'>Régimen <b>{html.escape(str(state.get('regime', '')))}</b></div>",
    ]
    st.markdown(f"<div class='cdf-strip'>{''.join(pills)}</div>", unsafe_allow_html=True)

    if day.get("halted_reason"):
        st.error(f"⛔ {day['halted_reason']}")
    if state.get("last_error"):
        st.warning(f"Último error: {state['last_error']}")

    k = st.columns(5)
    cur = acc.get("currency", "")
    k[0].metric("Balance", f"{acc.get('balance', 0):,.0f} {cur}")
    k[1].metric("Equity", f"{acc.get('equity', 0):,.0f} {cur}")
    k[2].metric("P/L del día", f"{state.get('daily_pnl_pct', 0):+.2f}%")
    k[3].metric("Operaciones hoy", f"{day.get('trades_today', 0)} / {state.get('config', {}).get('max_trades_per_day', '?')}")
    k[4].metric("Posiciones", f"{len(state.get('positions', []))} / {state.get('config', {}).get('max_open_positions', '?')}")

    left, right = st.columns([1.4, 1])
    with left:
        st.markdown("##### Posiciones abiertas")
        pos = pd.DataFrame(state.get("positions", []))
        if pos.empty:
            st.caption("Sin posiciones abiertas.")
        else:
            pos["dirección"] = pos["direction"].map({1: "COMPRA", -1: "VENTA"})
            st.dataframe(pos[["ticket", "key", "symbol", "dirección", "volume", "open_price", "sl", "tp", "profit"]]
                         .rename(columns={"key": "activo", "volume": "lotes", "open_price": "entrada",
                                          "profit": "P/L"}),
                         hide_index=True, width="stretch")
        st.markdown("##### Señales (velas cerradas)")
        sig = pd.DataFrame(state.get("signals", []))
        if not sig.empty:
            th = state.get("config", {}).get("entry_threshold", 70)
            sig["candidata"] = sig["score"].abs() >= th
            st.dataframe(sig[["key", "score", "verdict", "candidata", "stale", "bar"]]
                         .rename(columns={"key": "activo", "verdict": "veredicto", "stale": "atrasado",
                                          "bar": "vela 1H"}), hide_index=True, width="stretch")
    with right:
        st.markdown("##### Actividad reciente")
        icons = {"OPEN": "🟢", "CLOSE": "🔵", "SKIP": "⚪", "ERROR": "🔴", "HALT": "⛔", "EMERGENCY": "🚨",
                 "START": "▶️", "STOP": "⏹️", "DAY": "📅", "RECONNECT": "🔄"}
        for ev in read_log(60)[:40]:
            ts = str(ev.get("time", ""))[11:19]
            st.markdown(f"<div class='small'>{icons.get(ev.get('kind'), '•')} <span class='muted'>{ts}</span> "
                        f"{html.escape(str(ev.get('message', '')))}</div>", unsafe_allow_html=True)


live_panel()

st.markdown("---")
st.markdown("##### 🎛️ Controles")
control = read_control()
c1, c2, c3 = st.columns([1, 1, 1.4])
if control.get("paused"):
    if c1.button("▶ Reanudar nuevas entradas", width="stretch"):
        write_control(paused=False)
        st.rerun()
else:
    if c1.button("⏸ Pausar nuevas entradas", width="stretch",
                 help="El bot sigue vigilando pero no abre operaciones nuevas"):
        write_control(paused=True)
        st.rerun()
confirm = c2.checkbox("Confirmo cerrar TODAS las posiciones del bot")
if c3.button("🚨 PARADA DE EMERGENCIA", type="primary", width="stretch", disabled=not confirm):
    write_control(emergency_close=True)
    st.error("Orden enviada: el bot cerrará todas sus posiciones en segundos y quedará en pausa.")

with st.expander("📘 Cómo arrancar y configurar el bot", expanded=not read_state()):
    st.markdown(f"""
1. **Empieza en modo papel** (simulado, no necesita MT5): en `.env` deja `BOT_MODE=paper` y haz doble clic en
   **`iniciar_bot.bat`**. Déjalo varios días y revisa aquí los resultados.
2. **Demo MT5**: abre una cuenta demo en un bróker con MetaTrader 5, instala el terminal, inicia sesión y
   **activa el botón "Algo Trading"**. Luego `pip install MetaTrader5` (lo hace `iniciar_bot.bat`) y pon
   `BOT_MODE=demo`. El bot **se niega a operar** si la cuenta conectada no es demo.
3. **Real**: solo tras semanas de demo con resultados aceptables. Requiere `BOT_MODE=real` **y**
   `BOT_CONFIRM_REAL={config.BOT_REAL_CONFIRMATION}` en `.env`.

Paquete MetaTrader5 detectado en este equipo: **{'sí' if mt5_available() else 'no'}**.
Los nombres de los símbolos se detectan solos (XAUUSD, US500.cash, NAS100…). Si alguno falla, fíjalo con
`MT5_SYMBOL_US500=...` en `.env`. Todas las opciones están en `.env.example`.
""")

state = read_state()
if state.get("config"):
    with st.expander("⚙️ Configuración activa del bot"):
        st.json(state["config"])
    if state.get("symbols"):
        with st.expander("🔤 Símbolos del bróker"):
            st.json(state["symbols"])
