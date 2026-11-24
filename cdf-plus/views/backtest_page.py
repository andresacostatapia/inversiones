"""Página de Backtest: ¿la estrategia habría ganado dinero en el pasado?"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

import backtest as bt
import config
from brokers import BrokerError, MT5Broker, mt5_available

st.markdown("<div class='cdf-header'><span class='cdf-title'>Backtest</span>"
            "<span class='cdf-sub'>Prueba histórica de la estrategia antes de automatizarla</span></div>",
            unsafe_allow_html=True)
st.caption("Se recalcula el score vela a vela con las mismas reglas que la app y el bot, sin mirar el futuro: "
           "la señal se evalúa al cierre de la vela de 1H y se entra en la apertura de la siguiente.")


@st.cache_data(ttl=3600, show_spinner=False)
def load(source: str, horizon: str) -> tuple[dict, list[str]]:
    if source == "mt5":
        broker = MT5Broker("paper")
        broker.connect()
        try:
            return bt.load_history_mt5(broker)
        finally:
            broker.shutdown()
    return bt.load_history_yf(horizon)


@st.cache_data(ttl=3600, show_spinner=False)
def scores_for(source: str, horizon: str, use_intermarket: bool) -> dict:
    history, _ = load(source, horizon)
    return bt.compute_scores(history, use_intermarket=use_intermarket)


with st.form("bt_form"):
    c1, c2, c3, c4 = st.columns(4)
    sources = {"yfinance (futuros)": "yfinance"}
    if mt5_available():
        sources["MetaTrader 5 (tu bróker)"] = "mt5"
    source = sources[c1.selectbox("Datos", list(sources))]
    horizon_label = c2.selectbox("Horizonte", ["2 años (sin 15M, aproximado)", "60 días (estrategia completa)"],
                                 help="yfinance solo da 60 días de velas de 15m. Con MT5 se usa todo el historial "
                                      "disponible en el bróker.")
    horizon = bt.HORIZON_2Y if horizon_label.startswith("2") else bt.HORIZON_60D
    threshold = c3.slider("Umbral de entrada (±score)", 20, 95, 70, 5)
    risk_pct = c4.number_input("Riesgo por operación (%)", 0.1, 5.0, 0.5, 0.1)
    d1, d2, d3, d4 = st.columns(4)
    sl_mult = d1.number_input("SL × ATR", 0.3, 10.0, config.DEFAULT_SL_ATR_MULT, 0.1)
    tp_mult = d2.number_input("TP × ATR", 0.3, 20.0, config.DEFAULT_TP_ATR_MULT, 0.1)
    spread_mult = d3.number_input("Multiplicador de spread", 0.0, 5.0, 1.0, 0.5,
                                  help="1 = spread típico; 2 = el doble (prueba de estrés)")
    use_im = d4.toggle("Filtro intermercado", value=True)
    apply_limits = st.toggle("Aplicar límites de posiciones del bot (3 totales · 2 índices · 1 metal)", value=True)
    run = st.form_submit_button("▶ Ejecutar backtest", type="primary")

if run:
    st.session_state["bt_params"] = dict(source=source, horizon=horizon, threshold=threshold, risk_pct=risk_pct,
                                         sl_mult=sl_mult, tp_mult=tp_mult, spread_mult=spread_mult, use_im=use_im,
                                         apply_limits=apply_limits)

params = st.session_state.get("bt_params")
if not params:
    st.info("Configura los parámetros y pulsa **Ejecutar backtest**. La primera descarga tarda unos segundos.")
    st.stop()

try:
    with st.spinner("Descargando historial y calculando scores…"):
        history, notes = load(params["source"], params["horizon"])
        scores = scores_for(params["source"], params["horizon"], params["use_im"])
except (BrokerError, Exception) as exc:  # noqa: BLE001 - mostrar cualquier fallo de datos
    st.error(f"No se pudo preparar el backtest: {exc}")
    st.stop()
for n in notes:
    st.warning(n)
if not scores:
    st.error("Sin datos históricos.")
    st.stop()

start = None
if params["horizon"] == bt.HORIZON_60D:
    firsts = [df.index[df["s15"].notna().argmax()] for df in scores.values() if df["s15"].notna().any()]
    start = min(firsts) if firsts else None

p = bt.BacktestParams(threshold=float(params["threshold"]), sl_atr_mult=params["sl_mult"], tp_atr_mult=params["tp_mult"],
                      risk_pct=params["risk_pct"], spread_mult=params["spread_mult"],
                      apply_limits=params["apply_limits"], start=start)
res = bt.simulate(scores, p)
s = res.summary

level, text = bt.verdict(s)
{"good": st.success, "warn": st.warning, "bad": st.error}[level](text)

k = st.columns(7)
k[0].metric("Operaciones", f"{s['operaciones']}", f"{s['operaciones_mes']:.0f}/mes", delta_color="off")
k[1].metric("Acierto", f"{s['acierto_%']:.1f}%",
            f"equilibrio {100 / (1 + params['tp_mult'] / params['sl_mult']):.1f}%", delta_color="off")
k[2].metric("Profit factor", f"{s['profit_factor']:.2f}")
k[3].metric("Expectativa", f"{s['expectativa_R']:+.3f} R")
k[4].metric("R total", f"{s['R_total']:+.1f}")
k[5].metric("Retorno", f"{s['retorno_%']:+.1f}%")
k[6].metric("Máx. drawdown", f"{s['max_drawdown_%']:.1f}%")

if not res.trades.empty:
    fig = go.Figure(go.Scatter(x=res.equity.index, y=res.equity.values, mode="lines",
                               line=dict(color="#f5a623", width=1.6), name="Equity"))
    fig.add_hline(y=p.initial_equity, line_dash="dot", line_color="#4b5a6b")
    fig.update_layout(template="plotly_dark", height=330, margin=dict(l=10, r=10, t=30, b=10),
                      paper_bgcolor="#0b0f14", plot_bgcolor="#0e141c", title="Curva de capital (simulada)",
                      font=dict(family="JetBrains Mono, monospace", size=11))
    st.plotly_chart(fig, theme=None, config={"displaylogo": False})

c_left, c_right = st.columns([1, 1.3])
with c_left:
    st.markdown("##### Por activo")
    st.dataframe(res.per_asset.round(2), hide_index=True, width="stretch")
with c_right:
    st.markdown("##### Sensibilidad al umbral de entrada")
    sweep = bt.threshold_sweep(scores, p)
    st.dataframe(sweep[["umbral", "operaciones", "acierto_%", "profit_factor", "expectativa_R", "retorno_%",
                        "max_drawdown_%"]].round(2), hide_index=True, width="stretch")

with st.expander(f"Operaciones simuladas ({len(res.trades)})"):
    if res.trades.empty:
        st.caption("Ninguna operación con estos parámetros.")
    else:
        t = res.trades.copy()
        for col in ("R", "P/L", "equity", "entrada", "salida"):
            t[col] = t[col].astype(float).round(3)
        st.dataframe(t, hide_index=True, width="stretch")

span = [df.index[df["score"].notna()] for df in scores.values()]
first = min(x[0] for x in span if len(x)) if any(len(x) for x in span) else None
last = max(x[-1] for x in span if len(x)) if any(len(x) for x in span) else None
st.caption(f"Periodo analizado: {first:%Y-%m-%d} → {last:%Y-%m-%d}" if first is not None else "")
st.markdown(
    "<div class='disclaimer'>Limitaciones: tamaño de posición por % de riesgo exacto (sin redondeo a 0.01 lotes), "
    "spread típico fijo, sin swaps ni comisiones, sin deslizamiento. Con el horizonte de 2 años el bloque 15M no "
    "existe. Un resultado positivo no garantiza resultados futuros.</div>", unsafe_allow_html=True)
