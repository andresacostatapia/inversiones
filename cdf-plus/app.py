"""
app.py — CDF Plus · Copiloto de Trading CFD para XTB xStation (interfaz Streamlit).

Ejecución:
    streamlit run app.py

La app NO ejecuta órdenes: calcula señales, niveles y lotaje para que tú introduzcas la
orden manualmente en xStation.
"""

from __future__ import annotations

import html
import time
from datetime import datetime, timezone

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import config
import data_fetcher as dfetch
import indicators as ind
import notifier as notif
import risk_engine as risk
from config import ASSETS

st.set_page_config(
    page_title=f"{config.APP_NAME} · Copiloto CFD",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Estilos: terminal financiero oscuro
# ---------------------------------------------------------------------------
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap');
:root {
  --bg:#0b0f14; --panel:#111821; --panel2:#0e141c; --border:#1f2a37; --text:#d6dee8;
  --muted:#7d8a99; --amber:#f5a623; --cyan:#22d3ee; --green:#22c55e; --red:#ef4444; --warn:#eab308;
}
html, body, [class*="css"], .stMarkdown, .stText, code { font-family:'JetBrains Mono', ui-monospace, monospace !important; }
.block-container { padding-top: 3rem; padding-bottom: 2rem; max-width: 1600px; }
.cdf-header { display:flex; align-items:baseline; gap:14px; border-bottom:1px solid var(--border); padding-bottom:6px; margin-bottom:10px; }
.cdf-title { font-size:1.6rem; font-weight:700; color:var(--amber); letter-spacing:1px; }
.cdf-sub { color:var(--muted); font-size:.85rem; }
.cdf-strip { display:flex; flex-wrap:wrap; gap:10px; margin:6px 0 14px 0; }
.cdf-pill { background:var(--panel); border:1px solid var(--border); border-radius:6px; padding:6px 10px; font-size:.78rem; color:var(--text); }
.cdf-pill b { color:var(--amber); }
.cdf-regime { border-left:4px solid var(--amber); }
.cdf-table { width:100%; border-collapse:collapse; font-size:.82rem; background:var(--panel2); border:1px solid var(--border); }
.cdf-table th { text-align:left; color:var(--muted); font-weight:600; padding:8px 10px; border-bottom:1px solid var(--border); background:var(--panel); text-transform:uppercase; font-size:.7rem; letter-spacing:.6px; }
.cdf-table td { padding:8px 10px; border-bottom:1px solid #16202b; vertical-align:middle; }
.cdf-table tr:hover td { background:#131c27; }
.cdf-table tr.sel td { background:#18222f; }
.num { text-align:right; font-variant-numeric: tabular-nums; }
.up { color:var(--green); } .down { color:var(--red); } .muted { color:var(--muted); } .warnc { color:var(--warn); }
.chip { display:inline-block; padding:2px 7px; border-radius:4px; font-size:.7rem; font-weight:700; letter-spacing:.4px; }
.chip-bull { background:rgba(34,197,94,.15); color:var(--green); border:1px solid rgba(34,197,94,.4); }
.chip-bear { background:rgba(239,68,68,.15); color:var(--red); border:1px solid rgba(239,68,68,.4); }
.chip-flat { background:rgba(125,138,153,.12); color:var(--muted); border:1px solid rgba(125,138,153,.35); }
.chip-sbuy { background:var(--green); color:#04140a; }
.chip-ssell { background:var(--red); color:#1a0505; }
.chip-warn { background:rgba(234,179,8,.15); color:var(--warn); border:1px solid rgba(234,179,8,.4); }
.sbar { position:relative; width:90px; height:8px; background:#1a2430; border-radius:4px; display:inline-block; vertical-align:middle; margin-left:6px; }
.sbar span { position:absolute; top:0; height:8px; border-radius:4px; }
.sbar i { position:absolute; left:50%; top:-2px; width:1px; height:12px; background:#4b5a6b; }
.card { background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:14px 16px; margin-bottom:10px; }
.card h4 { margin:0 0 8px 0; color:var(--amber); font-size:.85rem; letter-spacing:1px; text-transform:uppercase; }
.ticket-row { display:flex; justify-content:space-between; align-items:baseline; gap:10px; padding:4px 0; border-bottom:1px dashed #1c2733; font-size:.85rem; }
.ticket-row > span:first-child { color:var(--muted); white-space:nowrap; }
.ticket-row > span:last-child { text-align:right; }
.ticket-big { font-size:1.15rem; font-weight:700; }
.side-buy { color:var(--green); } .side-sell { color:var(--red); }
.score-big { font-size:2.2rem; font-weight:700; line-height:1; }
.rule-tbl td { font-size:.78rem; padding:5px 8px; }
.small { font-size:.75rem; }
.disclaimer { color:var(--muted); font-size:.7rem; border-top:1px solid var(--border); margin-top:24px; padding-top:8px; }
div[data-testid="stCode"] pre { font-size: 1rem !important; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Recursos compartidos
# ---------------------------------------------------------------------------
@st.cache_resource
def get_offset_store() -> risk.OffsetStore:
    return risk.OffsetStore()


@st.cache_resource
def get_alert_dedup() -> notif.AlertDeduplicator:
    return notif.AlertDeduplicator()


def get_notifier() -> notif.TelegramNotifier:
    return notif.TelegramNotifier()


def init_state() -> None:
    defaults: dict[str, object] = {
        "cache_buster": 0,
        "selected_asset": "GOLD",
        "alert_log": [],
        "alert_failures": {},
        "last_full_run": time.time(),
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)
    st.session_state["last_full_run"] = time.time()


# ---------------------------------------------------------------------------
# Helpers de formato
# ---------------------------------------------------------------------------
def fmt_price(value: float | None, digits: int) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value:,.{digits}f}"


def fmt_signed(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value:+,.{digits}f}{suffix}"


def bias_chip(bias: str) -> str:
    cls = {"ALCISTA": "chip-bull", "BAJISTA": "chip-bear"}.get(bias, "chip-flat")
    short = {"ALCISTA": "▲ ALC", "BAJISTA": "▼ BAJ", "LATERAL": "◆ LAT"}.get(bias, "—")
    return f'<span class="chip {cls}">{short}</span>'


def verdict_chip(verdict: str) -> str:
    cls = {
        ind.VERDICT_STRONG_BUY: "chip-sbuy", ind.VERDICT_BUY: "chip-bull",
        ind.VERDICT_SELL: "chip-bear", ind.VERDICT_STRONG_SELL: "chip-ssell",
    }.get(verdict, "chip-flat")
    return f'<span class="chip {cls}">{html.escape(verdict)}</span>'


def score_bar(score: float) -> str:
    hw = min(abs(score), 100.0) / 2.0
    left = 50.0 if score >= 0 else 50.0 - hw
    color = "var(--green)" if score >= 0 else "var(--red)"
    return f'<span class="sbar"><i></i><span style="left:{left:.1f}%;width:{hw:.1f}%;background:{color}"></span></span>'


def color_cls(value: float | None) -> str:
    if value is None or not np.isfinite(value) or value == 0:
        return "muted"
    return "up" if value > 0 else "down"


# ---------------------------------------------------------------------------
# Barra lateral
# ---------------------------------------------------------------------------
def render_sidebar() -> dict[str, object]:
    sb = st.sidebar
    sb.markdown(f"### 📈 {config.APP_NAME}")
    sb.caption(config.APP_SUBTITLE)

    # --- Datos ---
    sb.markdown("#### 🛰️ Datos")
    pref_label = sb.selectbox("Fuente de datos", ["Automática", "Twelve Data", "yfinance"], index=0,
                              help="Automática: Twelve Data si hay API key en .env; si no, yfinance.")
    preference = {"Automática": dfetch.PROVIDER_AUTO, "Twelve Data": dfetch.PROVIDER_TWELVE,
                  "yfinance": dfetch.PROVIDER_YF}[pref_label]
    has_key = dfetch.get_twelve_api_key() is not None
    effective = dfetch.resolve_provider(preference)
    if effective == dfetch.PROVIDER_TWELVE:
        sb.success(f"Twelve Data · API key detectada · TTL {config.TWELVE_CACHE_TTL_S // 60} min")
    else:
        if preference == dfetch.PROVIDER_TWELVE and not has_key:
            sb.warning("No hay TWELVE_DATA_API_KEY en .env → usando yfinance")
        sb.info(f"yfinance (sin API key) · TTL {config.YF_CACHE_TTL_S // 60} min")

    # --- Actualización ---
    if sb.button("🔄 Actualizar ahora", width="stretch", help="Descarga datos nuevos ignorando la caché"):
        dfetch.clear_cache()
        st.session_state["cache_buster"] = int(st.session_state["cache_buster"]) + 1
    auto = sb.toggle("Auto-actualización", value=False, help="Recarga automática de la app")
    interval_min = sb.select_slider("Intervalo de auto-actualización (min)", options=[1, 2, 5, 10, 15],
                                    value=5, disabled=not auto)

    # --- Cuenta y riesgo ---
    sb.markdown("#### 💼 Cuenta y riesgo")
    capital = sb.number_input("Capital de la cuenta (USD)", min_value=0.0, value=config.DEFAULT_CAPITAL_USD,
                              step=500.0, format="%.2f")
    risk_pct = sb.number_input("Riesgo máximo por operación (%)", min_value=0.0, max_value=config.MAX_RISK_PCT,
                               value=config.DEFAULT_RISK_PCT, step=0.1, format="%.2f")
    s1, s2 = sb.columns(2)
    sl_mult = s1.number_input("SL × ATR", min_value=0.1, max_value=10.0, value=config.DEFAULT_SL_ATR_MULT,
                              step=0.1, format="%.1f")
    tp_mult = s2.number_input("TP × ATR", min_value=0.1, max_value=20.0, value=config.DEFAULT_TP_ATR_MULT,
                              step=0.1, format="%.1f")
    atr_tf = sb.selectbox("Temporalidad del ATR", list(config.TIMEFRAMES),
                          index=list(config.TIMEFRAMES).index(config.OPERATIONAL_TF))
    settings = risk.RiskSettings(capital_usd=capital, risk_pct=risk_pct, sl_atr_mult=sl_mult, tp_atr_mult=tp_mult)
    sb.caption(f"Riesgo por operación: **${settings.risk_usd:,.2f}** · Ratio R:B **1:{settings.rr_ratio:.2f}**")

    # --- Especificaciones de contrato ---
    point_values: dict[str, float] = {}
    with sb.expander("📐 Especificaciones de contrato (XTB)"):
        st.caption("USD por 1.0 punto con 1.0 lote. Verifícalo en xStation → Información del instrumento.")
        for key, a in ASSETS.items():
            point_values[key] = st.number_input(f"{key} · USD/punto/lote", min_value=0.0001,
                                                value=float(a.point_value_usd), step=1.0, key=f"pv_{key}")

    # --- Alertas ---
    sb.markdown("#### 🔔 Alertas Telegram")
    notifier = get_notifier()
    alerts_on = sb.toggle("Enviar alertas", value=False, disabled=not notifier.is_configured)
    threshold = sb.slider("Umbral de score (±)", min_value=30, max_value=100,
                          value=int(config.DEFAULT_ALERT_THRESHOLD), step=5)
    if notifier.is_configured:
        if sb.button("Enviar mensaje de prueba", width="stretch"):
            res = notifier.send(f"✅ <b>{config.APP_NAME}</b>: conexión con Telegram correcta.")
            (sb.success if res.ok else sb.error)(res.message)
    else:
        sb.caption("Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env para activarlas.")
    if alerts_on and not auto:
        sb.caption("ℹ️ Activa *Auto* para que las alertas se evalúen sin interacción.")

    # --- Calibración ---
    sb.markdown("#### 🎯 Calibración")
    if sb.button("Restablecer todos los offsets", width="stretch"):
        get_offset_store().reset()
        sb.success("Offsets eliminados")

    return {
        "preference": preference, "auto": auto, "interval_min": interval_min,
        "settings": settings, "atr_tf": atr_tf, "point_values": point_values,
        "alerts_on": alerts_on, "threshold": float(threshold),
    }


# ---------------------------------------------------------------------------
# Cálculos de apoyo
# ---------------------------------------------------------------------------
def offset_for(store: risk.OffsetStore, data: dfetch.AssetData) -> tuple[float, risk.OffsetCalibration | None]:
    cal = store.get(data.key, data.provider, data.feed_symbol) if data.ok else None
    return (cal.offset if cal else 0.0), cal


def make_ticket(key: str, direction: int, data: dfetch.AssetData, sig: ind.SignalResult,
                store: risk.OffsetStore, ui: dict[str, object]) -> risk.TradeTicket:
    offset, cal = offset_for(store, data)
    point_values: dict[str, float] = ui["point_values"]  # type: ignore[assignment]
    return risk.build_ticket(
        ASSETS[key], direction, data.last_price, sig.atr(str(ui["atr_tf"])), offset,
        ui["settings"], calibrated=cal is not None, point_value_usd=point_values.get(key),  # type: ignore[arg-type]
    )


ALERT_RETRY_S = 300.0


def dispatch_alerts(snapshot: dfetch.MarketSnapshot, signals: dict[str, ind.SignalResult],
                    ctx: ind.IntermarketContext, store: risk.OffsetStore, ui: dict[str, object]) -> None:
    """Envía alertas de Telegram para señales que superan el umbral (sin duplicar por vela)."""
    if not ui["alerts_on"]:
        return
    notifier = get_notifier()
    if not notifier.is_configured:
        return
    dedup = get_alert_dedup()
    for key, sig in signals.items():
        data = snapshot.assets[key]
        if not sig.ok or data.is_stale:
            continue
        direction = notif.should_alert(sig.score, float(ui["threshold"]))  # type: ignore[arg-type]
        if direction == 0:
            continue
        akey = dedup.make_key(key, direction, sig.signal_bar_time())
        if dedup.already_sent(akey):
            continue
        failures: dict[str, float] = st.session_state["alert_failures"]
        if time.time() - failures.get(akey, 0.0) < ALERT_RETRY_S:
            continue  # Un envío fallido se reintenta como mucho cada ALERT_RETRY_S
        t = make_ticket(key, direction, data, sig, store, ui)
        lines = [
            f"{t.side_label} · {t.lots:.2f} lotes",
            f"Entrada: {t.fmt(t.entry)}",
            f"Stop Loss: {t.fmt(t.stop_loss)}",
            f"Take Profit: {t.fmt(t.take_profit)}",
            f"Riesgo: ${t.risk_usd_real:,.2f} · R:B 1:{t.rr_ratio:.2f}",
            "Precios calibrados a xStation" if t.calibrated else "⚠️ Sin calibrar (precio del feed)",
        ]
        biases = {tf.upper(): tfa.bias for tf, tfa in sig.timeframes.items()}
        text = notif.format_signal_alert(key, ASSETS[key].name, sig.score, sig.verdict, lines,
                                         ctx.regime_label, biases)
        res = notifier.send(text)
        if res.ok:
            dedup.mark_sent(akey)
            failures.pop(akey, None)
        else:
            failures[akey] = time.time()
        log: list[str] = st.session_state["alert_log"]
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log.insert(0, f"{stamp} UTC · {key} {sig.verdict} ({sig.score:+.0f}) → {res.message}")
        del log[20:]


# ---------------------------------------------------------------------------
# Secciones de la interfaz
# ---------------------------------------------------------------------------
def render_header(snapshot: dfetch.MarketSnapshot, ctx: ind.IntermarketContext) -> None:
    providers = ", ".join(dfetch.PROVIDER_LABELS.get(p, p) for p in sorted(snapshot.providers_used)) or "—"
    updated = snapshot.fetched_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    st.markdown(
        f'<div class="cdf-header"><span class="cdf-title">{config.APP_NAME}</span>'
        f'<span class="cdf-sub">{config.APP_SUBTITLE}</span></div>',
        unsafe_allow_html=True,
    )
    eq_cls, mt_cls = color_cls(ctx.equity_score), color_cls(ctx.metals_score)
    regime_cls = {"RISK_ON": "up", "RISK_OFF": "down"}.get(ctx.regime, "warnc")
    pills = [
        f'<div class="cdf-pill cdf-regime">Régimen <b class="{regime_cls}">{html.escape(ctx.regime_label)}</b></div>',
        f'<div class="cdf-pill">Índices USA <b class="{eq_cls}">{ctx.equity_score:+.0f}</b></div>',
        f'<div class="cdf-pill">Metales <b class="{mt_cls}">{ctx.metals_score:+.0f}</b></div>',
        f'<div class="cdf-pill">Amplitud <b>{html.escape(ctx.breadth_label)}</b></div>',
        f'<div class="cdf-pill">Feed <b>{html.escape(providers)}</b></div>',
        f'<div class="cdf-pill">Actualizado <b>{updated}</b></div>',
    ]
    if snapshot.twelve_requests:
        pills.append(f'<div class="cdf-pill">Créditos TD <b>{snapshot.twelve_requests}</b></div>')
    st.markdown(f'<div class="cdf-strip">{"".join(pills)}</div>', unsafe_allow_html=True)


def render_scanner(snapshot: dfetch.MarketSnapshot, signals: dict[str, ind.SignalResult],
                   store: risk.OffsetStore, ui: dict[str, object], selected: str) -> None:
    st.markdown("##### 🧭 Escáner de mercado")
    atr_tf = str(ui["atr_tf"])
    head = ("<tr><th>Activo</th><th class='num'>Precio XTB</th><th class='num'>Var. día</th>"
            "<th>15M</th><th>1H</th><th>4H</th><th class='num'>ATR " + atr_tf.upper() + "</th>"
            "<th>Score</th><th>Veredicto</th><th>Estado</th></tr>")
    rows: list[str] = []
    for key, a in ASSETS.items():
        data, sig = snapshot.assets[key], signals[key]
        sel = " class='sel'" if key == selected else ""
        name = (f"<b>{key}</b><br><span class='muted small'>{html.escape(a.name)}</span>")
        if not data.ok:
            err = html.escape((data.error or "Sin datos")[:90])
            rows.append(f"<tr{sel}><td>{name}</td><td colspan='9' class='down small'>⚠ {err}</td></tr>")
            continue
        offset, cal = offset_for(store, data)
        price = (data.last_price or 0.0) + offset
        cal_mark = "<span class='up' title='Calibrado'>●</span>" if cal else "<span class='warnc' title='Sin calibrar'>○</span>"
        chg = data.change_pct
        tf_cells = "".join(
            f"<td>{bias_chip(sig.timeframes[tf].bias) if tf in sig.timeframes and sig.timeframes[tf].ok else '—'}</td>"
            for tf in config.TIMEFRAMES
        )
        atr_v = sig.atr(atr_tf)
        status: list[str] = []
        status.append("<span class='warnc'>Cerrado/atrasado</span>" if data.is_stale else "<span class='up'>En vivo</span>")
        status.append(f"<span class='muted'>{html.escape(data.feed_symbol)}</span>")
        score_html = (f"<b class='{color_cls(sig.score)}'>{sig.score:+.0f}</b>{score_bar(sig.score)}"
                      if sig.ok else "—")
        rows.append(
            f"<tr{sel}><td>{name}</td>"
            f"<td class='num'>{cal_mark} <b>{fmt_price(price, a.digits)}</b></td>"
            f"<td class='num {color_cls(chg)}'>{fmt_signed(chg, 2, '%')}</td>"
            f"{tf_cells}"
            f"<td class='num'>{fmt_price(atr_v, a.digits)}</td>"
            f"<td>{score_html}</td>"
            f"<td>{verdict_chip(sig.verdict) if sig.ok else '—'}</td>"
            f"<td class='small'>{' · '.join(status)}</td></tr>"
        )
    st.markdown(f"<table class='cdf-table'>{head}{''.join(rows)}</table>", unsafe_allow_html=True)
    st.caption("● calibrado con precio de xStation · ○ sin calibrar (precio del feed escalado)")


def render_bulk_calibrator(snapshot: dfetch.MarketSnapshot, store: risk.OffsetStore) -> None:
    with st.expander("🎯 Calibrador de Offset XTB · los 6 activos", expanded=False):
        st.caption("Escribe el precio que ves AHORA en xStation y pulsa *Calibrar*. "
                   "offset = precio_xtb − precio_feed. Deja vacío lo que no quieras cambiar.")
        with st.form("bulk_calibration", border=False):
            cols = st.columns(3)
            inputs: dict[str, float | None] = {}
            for i, (key, a) in enumerate(ASSETS.items()):
                data = snapshot.assets[key]
                offset, cal = offset_for(store, data)
                with cols[i % 3]:
                    feed_txt = fmt_price(data.last_price, a.digits) if data.ok else "sin datos"
                    inputs[key] = st.number_input(
                        f"{key} · feed {feed_txt}", value=None, min_value=0.0, step=10 ** -a.digits,
                        format=f"%.{a.digits}f", placeholder="Precio xStation", key=f"bulk_{key}",
                        disabled=not data.ok,
                    )
                    st.caption(f"Offset actual: {fmt_signed(offset, a.digits) if cal else 'sin calibrar'}")
            submitted = st.form_submit_button("Calibrar", type="primary")
        if submitted:
            done: list[str] = []
            for key, value in inputs.items():
                data = snapshot.assets[key]
                if value is None or value <= 0 or not data.ok or data.last_price is None:
                    continue
                cal = store.calibrate(key, value, data.last_price, data.provider, data.feed_symbol)
                done.append(f"{key} ({cal.offset:+.{ASSETS[key].digits}f})")
            if done:
                st.success("Calibrados: " + ", ".join(done))
                st.rerun()
            else:
                st.info("No se introdujo ningún precio.")


def build_chart(sig: ind.SignalResult, tf: str, offset: float, digits: int,
                ticket: risk.TradeTicket | None) -> go.Figure | None:
    tfa = sig.timeframes.get(tf)
    if tfa is None or not tfa.ok or tfa.df is None:
        return None
    df = tfa.df.tail(config.CHART_BARS).copy()
    for col in ("open", "high", "low", "close", "ema20", "ema50", "ema200"):
        df[col] = df[col] + offset
    x = df.index.strftime("%d-%b %H:%M")

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                        row_heights=[0.62, 0.18, 0.20])
    fig.add_trace(go.Candlestick(x=x, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
                                 name="Precio", increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
                                 increasing_fillcolor="#22c55e", decreasing_fillcolor="#ef4444"), row=1, col=1)
    for col, color, name in (("ema20", "#22d3ee", "EMA 20"), ("ema50", "#f5a623", "EMA 50"),
                             ("ema200", "#a78bfa", "EMA 200")):
        fig.add_trace(go.Scatter(x=x, y=df[col], mode="lines", name=name, line=dict(color=color, width=1.4)),
                      row=1, col=1)

    if ticket is not None and ticket.valid:
        for y, color, label in ((ticket.entry, "#e5e7eb", "ENTRADA"), (ticket.stop_loss, "#ef4444", "SL"),
                                (ticket.take_profit, "#22c55e", "TP")):
            fig.add_hline(y=y, line_dash="dash" if label != "ENTRADA" else "dot", line_color=color, line_width=1.3,
                          annotation_text=f"{label} {y:,.{digits}f}", annotation_position="right",
                          annotation_font_color=color, annotation_font_size=11, row=1, col=1)

    fig.add_trace(go.Scatter(x=x, y=df["rsi"], mode="lines", name="RSI 14", line=dict(color="#f5a623", width=1.2)),
                  row=2, col=1)
    for lvl in (30, 50, 70):
        fig.add_hline(y=lvl, line_dash="dot", line_color="#3b4756", line_width=1, row=2, col=1)

    hist_colors = np.where(df["macd_hist"] >= 0, "#22c55e", "#ef4444")
    fig.add_trace(go.Bar(x=x, y=df["macd_hist"], name="Hist MACD", marker_color=hist_colors, opacity=0.6),
                  row=3, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["macd"], mode="lines", name="MACD", line=dict(color="#22d3ee", width=1.2)),
                  row=3, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["macd_signal"], mode="lines", name="Señal",
                             line=dict(color="#f472b6", width=1.2)), row=3, col=1)

    fig.update_layout(
        template="plotly_dark", height=640, margin=dict(l=10, r=150, t=30, b=10),
        paper_bgcolor="#0b0f14", plot_bgcolor="#0e141c", font=dict(family="JetBrains Mono, monospace", size=11),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0, font=dict(size=10)),
        xaxis_rangeslider_visible=False, hovermode="x unified", bargap=0.1,
    )
    fig.update_xaxes(type="category", nticks=10, showgrid=False)
    fig.update_yaxes(gridcolor="#18222e", zeroline=False)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)
    fig.update_yaxes(title_text="MACD", row=3, col=1)
    return fig


def render_ticket(t: risk.TradeTicket, cal: risk.OffsetCalibration | None, settings: risk.RiskSettings) -> None:
    side_cls = "side-buy" if t.direction > 0 else "side-sell" if t.direction < 0 else "muted"
    rows: list[tuple[str, str]] = []
    if t.valid:
        pct_real = (t.risk_usd_real / settings.capital_usd * 100) if settings.capital_usd > 0 else 0.0
        rows = [
            ("Instrumento", f"<b>{t.key}</b> · orden a mercado"),
            ("Volumen", f"<span class='ticket-big'>{t.lots:.2f}</span> lotes"),
            ("Entrada (ref.)", f"<span class='ticket-big'>{t.fmt(t.entry)}</span>"),
            ("Stop Loss", f"<span class='ticket-big down'>{t.fmt(t.stop_loss)}</span> "
                          f"<span class='muted small'>({t.sl_distance:,.{t.digits}f} pts · {settings.sl_atr_mult:.1f}×ATR)</span>"),
            ("Take Profit", f"<span class='ticket-big up'>{t.fmt(t.take_profit)}</span> "
                            f"<span class='muted small'>({t.tp_distance:,.{t.digits}f} pts · {settings.tp_atr_mult:.1f}×ATR)</span>"),
            ("Riesgo real", f"<b class='down'>${t.risk_usd_real:,.2f}</b> "
                            f"<span class='muted small'>({pct_real:.2f}% · objetivo ${t.risk_usd_target:,.2f})</span>"),
            ("Beneficio potencial", f"<b class='up'>${t.reward_usd:,.2f}</b> · R:B 1:{t.rr_ratio:.2f}"),
            ("Margen estimado", f"${t.margin_usd:,.2f} <span class='muted small'>(nominal ${t.nominal_usd:,.0f})</span>"),
            ("ATR usado", f"{t.atr:,.{t.digits}f} pts"),
            ("Offset XTB", (f"{t.offset:+,.{t.digits}f} <span class='muted small'>(hace {cal.age_hours:.1f} h)</span>"
                            if cal else "<span class='warnc'>sin calibrar</span>")),
        ]
    body = "".join(f"<div class='ticket-row'><span>{k}</span><span>{v}</span></div>" for k, v in rows)
    errors = "".join(f"<div class='down small'>✖ {html.escape(e)}</div>" for e in t.errors)
    st.markdown(
        f"<div class='card'><h4>🎫 Ticket listo para XTB</h4>"
        f"<div class='ticket-big {side_cls}' style='margin-bottom:6px'>{t.side_label}</div>{body}{errors}</div>",
        unsafe_allow_html=True,
    )
    if t.valid:
        st.caption("Copia con un clic (icono a la derecha de cada valor):")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("<span class='small muted'>Volumen</span>", unsafe_allow_html=True)
            st.code(f"{t.lots:.2f}", language=None)
            st.markdown("<span class='small muted'>Stop Loss</span>", unsafe_allow_html=True)
            st.code(t.fmt(t.stop_loss), language=None)
        with c2:
            st.markdown("<span class='small muted'>Entrada (ref.)</span>", unsafe_allow_html=True)
            st.code(t.fmt(t.entry), language=None)
            st.markdown("<span class='small muted'>Take Profit</span>", unsafe_allow_html=True)
            st.code(t.fmt(t.take_profit), language=None)
    for w in t.warnings:
        st.warning(w, icon="⚠️")


def render_rules(sig: ind.SignalResult) -> None:
    direction = 1 if sig.technical_score > 0 else -1 if sig.technical_score < 0 else 0
    icons = {ind.BULL: "<span class='up'>▲</span>", ind.BEAR: "<span class='down'>▼</span>",
             ind.WARN: "<span class='warnc'>⚠</span>", ind.NEUTRAL: "<span class='muted'>•</span>"}
    rows: list[str] = []
    for r in sig.rules:
        if r.block == "INTERMERCADO":
            favor = ("<span class='up'>✔</span>" if abs(sig.score) > abs(sig.technical_score)
                     else "<span class='down'>✖</span>" if abs(sig.score) < abs(sig.technical_score)
                     else "<span class='muted'>—</span>")
        elif direction == 0 or r.points == 0:
            favor = "<span class='muted'>—</span>"
        else:
            favor = "<span class='up'>✔</span>" if np.sign(r.points) == direction else "<span class='down'>✖</span>"
        rows.append(
            f"<tr><td class='muted'>{r.block}</td><td>{icons.get(r.status, '')} {html.escape(r.name)}</td>"
            f"<td class='small'>{html.escape(r.detail)}</td>"
            f"<td class='num {color_cls(r.points)}'>{r.points:+.1f}</td>"
            f"<td class='num muted'>{r.max_points:.0f}</td><td>{favor}</td></tr>"
        )
    head = ("<tr><th>Bloque</th><th>Regla</th><th>Detalle</th><th class='num'>Pts</th>"
            "<th class='num'>Máx</th><th>A favor</th></tr>")
    st.markdown(f"<table class='cdf-table rule-tbl'>{head}{''.join(rows)}</table>", unsafe_allow_html=True)
    st.caption(f"Score técnico {sig.technical_score:+.1f} × intermercado {sig.multiplier:.2f} = "
               f"**{sig.score:+.1f}**. ✔ = la regla apoya la dirección de la señal, ✖ = la contradice.")


def render_detail(snapshot: dfetch.MarketSnapshot, signals: dict[str, ind.SignalResult],
                  ctx: ind.IntermarketContext, store: risk.OffsetStore, ui: dict[str, object], key: str) -> None:
    a = ASSETS[key]
    data, sig = snapshot.assets[key], signals[key]
    st.markdown("---")
    if not data.ok or not sig.ok:
        st.error(f"{key}: {data.error or sig.error or 'sin datos'}. Prueba a actualizar o cambia de fuente.")
        return

    # Cabecera del activo
    h1, h2, h3 = st.columns([2.2, 1, 1.4])
    offset, cal = offset_for(store, data)
    with h1:
        st.markdown(
            f"<div><span class='cdf-title'>{key}</span> <span class='cdf-sub'>{html.escape(a.name)} · feed "
            f"{html.escape(data.feed_symbol)} ({dfetch.PROVIDER_LABELS.get(data.provider, data.provider)}"
            f"{' ×' + str(data.scale) if data.scale != 1 else ''})</span></div>"
            f"<div class='small muted'>Última vela: {data.last_bar_time:%Y-%m-%d %H:%M} UTC</div>",
            unsafe_allow_html=True,
        )
    with h2:
        st.markdown(f"<div class='score-big {color_cls(sig.score)}'>{sig.score:+.0f}</div>"
                    f"<div>{verdict_chip(sig.verdict)}</div>", unsafe_allow_html=True)
    with h3:
        mode = st.segmented_control("Dirección del ticket", ["Automática", "Compra", "Venta"],
                                    default="Automática", key=f"dir_{key}") or "Automática"
    direction = {"Automática": sig.direction, "Compra": 1, "Venta": -1}[mode]

    for w in data.warnings:
        st.warning(w, icon="🕒")

    left, right = st.columns([2.3, 1])
    with right:
        # Calibrador del activo seleccionado
        with st.form(f"cal_{key}", border=True):
            st.markdown("<h4 style='color:var(--amber);font-size:.85rem;margin:0'>🎯 CALIBRAR OFFSET</h4>",
                        unsafe_allow_html=True)
            st.caption(f"Feed escalado: {fmt_price(data.last_price, a.digits)} · "
                       f"offset actual: {fmt_signed(offset, a.digits) if cal else 'sin calibrar'}")
            price_in = st.number_input("Precio actual en xStation", value=None, min_value=0.0,
                                       step=10 ** -a.digits, format=f"%.{a.digits}f",
                                       placeholder="Precio en xStation", key=f"xtb_{key}")
            cc1, cc2 = st.columns(2)
            do_cal = cc1.form_submit_button("Calibrar", type="primary", width="stretch")
            do_reset = cc2.form_submit_button("Quitar", width="stretch")
        if do_cal:
            if price_in is None or price_in <= 0 or data.last_price is None:
                st.error("Introduce un precio válido de xStation")
            else:
                store.calibrate(key, price_in, data.last_price, data.provider, data.feed_symbol)
                st.rerun()
        if do_reset:
            store.reset(key)
            st.rerun()
        if cal and cal.age_hours > 12:
            st.info(f"La calibración tiene {cal.age_hours:.0f} h: recalibra si el diferencial ha cambiado "
                    "(rollover de futuros, apertura de sesión…).")

        ticket = make_ticket(key, direction, data, sig, store, ui)
        render_ticket(ticket, cal, ui["settings"])  # type: ignore[arg-type]

    with left:
        chart_tf = st.segmented_control("Temporalidad del gráfico", list(config.TIMEFRAMES),
                                        default=config.OPERATIONAL_TF, key=f"tf_{key}") or config.OPERATIONAL_TF
        fig = build_chart(sig, chart_tf, offset, a.digits, ticket)
        if fig is None:
            st.info("Sin datos suficientes para esta temporalidad.")
        else:
            st.plotly_chart(fig, theme=None, config={"displaylogo": False})

    b1, b2 = st.columns([2.3, 1])
    with b1:
        st.markdown("##### 🧮 Desglose del score")
        render_rules(sig)
    with b2:
        st.markdown("##### 🌐 Filtro intermercado")
        notes = "".join(f"<li>{html.escape(n)}</li>" for n in sig.intermarket_notes) or "<li>Sin ajustes</li>"
        trends = "".join(
            f"<div class='ticket-row'><span>{k}</span><span class='{color_cls(v)}'>{v:+.0f}</span></div>"
            for k, v in ctx.trends.items()
        )
        st.markdown(f"<div class='card'><h4>{html.escape(ctx.regime_label)}</h4><ul class='small'>{notes}</ul>"
                    f"<div class='small muted' style='margin-top:6px'>Tendencia de fondo (media 1H/4H):</div>"
                    f"{trends}</div>", unsafe_allow_html=True)
        st.markdown("##### 📊 Indicadores")
        rows = []
        for tf in config.TIMEFRAMES:
            t = sig.timeframes.get(tf)
            if t is None or not t.ok:
                continue
            v = t.values
            rows.append(
                f"<tr><td><b>{tf.upper()}</b></td><td class='num'>{v['rsi']:.1f}</td>"
                f"<td class='num {color_cls(v['macd_hist'])}'>{v['macd_hist']:+.2f}</td>"
                f"<td class='num'>{v['atr']:,.{a.digits}f}</td><td>{bias_chip(t.bias)}</td></tr>"
            )
        st.markdown("<table class='cdf-table rule-tbl'><tr><th>TF</th><th class='num'>RSI</th>"
                    "<th class='num'>Hist</th><th class='num'>ATR</th><th>Sesgo</th></tr>"
                    f"{''.join(rows)}</table>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Auto-refresco: fragmento ligero que relanza la app completa cada N minutos
# ---------------------------------------------------------------------------
def install_auto_refresh(enabled: bool, interval_min: int) -> None:
    if not enabled:
        return
    interval_s = max(30, interval_min * 60)

    @st.fragment(run_every=interval_s)
    def _ticker() -> None:
        elapsed = time.time() - float(st.session_state.get("last_full_run", 0.0))
        if elapsed >= interval_s - 1:
            st.rerun()  # Rerun completo: vuelve a evaluar datos (según TTL), señales y alertas
        st.caption(f"⏱️ Auto-actualización activa · cada {interval_min} min "
                   f"(los datos se renuevan según la caché del proveedor)")

    _ticker()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    init_state()
    ui = render_sidebar()
    store = get_offset_store()

    with st.spinner("Descargando datos de mercado de los 6 activos…"):
        try:
            snapshot = dfetch.get_market_snapshot(str(ui["preference"]), int(st.session_state["cache_buster"]))
        except Exception as exc:  # Error catastrófico de red: mostrar y no romper
            st.error(f"No se pudieron obtener datos: {exc}")
            st.stop()

    signals, ctx = ind.analyze_market(snapshot)
    dispatch_alerts(snapshot, signals, ctx, store, ui)

    render_header(snapshot, ctx)
    install_auto_refresh(bool(ui["auto"]), int(ui["interval_min"]))  # type: ignore[arg-type]

    if not any(a.ok for a in snapshot.assets.values()):
        st.error("Ningún activo devolvió datos. Revisa tu conexión a Internet o la API key y pulsa Actualizar.")
        for k, a in snapshot.assets.items():
            st.caption(f"{k}: {a.error}")
        st.stop()

    # El selector se dibuja después del escáner, pero su valor se lee antes para resaltar la fila
    selected = str(st.session_state.get("asset_picker") or st.session_state["selected_asset"])
    st.session_state["selected_asset"] = selected
    render_scanner(snapshot, signals, store, ui, selected)
    render_bulk_calibrator(snapshot, store)
    st.segmented_control("🔍 Vista detallada del activo", list(ASSETS.keys()),
                         default=selected, key="asset_picker")
    render_detail(snapshot, signals, ctx, store, ui, selected)

    log: list[str] = st.session_state["alert_log"]
    if log:
        with st.expander(f"🔔 Registro de alertas ({len(log)})"):
            for line in log:
                st.text(line)

    st.markdown(
        "<div class='disclaimer'>CDF Plus es una herramienta de apoyo a la decisión. No constituye asesoramiento "
        "financiero. Los CFDs son instrumentos complejos con alto riesgo de perder dinero rápidamente debido al "
        "apalancamiento. Verifica siempre precio, lotaje y especificaciones en xStation antes de ejecutar.</div>",
        unsafe_allow_html=True,
    )


main()
