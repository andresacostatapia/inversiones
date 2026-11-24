"""Orquestación del copiloto CFD: ajustes, calibraciones, escáner, detalle y alertas de Telegram."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..db import AlertRow, CfdState
from ..market_hours import is_open
from .analysis import (Regime, TfAnalysis, analyze_tf, clip, composite, intermarket, mtf_plan, regime, regime_tf,
                       verdict, with_indicators)
from .assets import OPERABLE, TF_DELTA, TF_LABEL, TIMEFRAMES, AssetOverride, CfdAsset
from .feed import SOURCE_NAMES, CfdFeed, FeedBars
from .risk import Calibration, TicketInput, build_ticket, calibration_status, resolve_offset

log = logging.getLogger(__name__)

DISCLAIMER = ("Herramienta educativa, no es asesoría financiera. Los CFD son apalancados: puedes perder más rápido "
              "de lo que crees. El copiloto no ejecuta órdenes: tú decides y operas a mano en xStation.")
FETCH_TFS = ["1m", "5m", "15m", "1h", "1d"]   # lo que se pide a las fuentes (4h se arma con 1h)
CHART_BARS = 300


class CfdSettings(BaseModel):
    """Ajustes que se cambian desde la pestaña CFD (se guardan en la base)."""

    capital_usd: float = Field(1000.0, gt=0, description="capital de la cuenta en USD")
    risk_pct: float = Field(1.0, gt=0, le=10, description="riesgo por operación, % del capital")
    sl_atr: float = Field(1.5, gt=0, le=20, description="stop = k × ATR")
    tp_atr: float = Field(3.0, gt=0, le=50, description="objetivo = k × ATR")
    timeframe: Literal["1m", "5m", "15m", "1h", "4h"] = "5m"
    alerts_enabled: bool = False
    alert_threshold: float = Field(70, ge=30, le=100)
    signal_threshold: float = Field(30, ge=5, le=69)
    assets: dict[str, AssetOverride] = Field(default_factory=dict)  # valor del punto, spread, margen…


class SettingsPatch(BaseModel):
    capital_usd: float | None = Field(None, gt=0)
    risk_pct: float | None = Field(None, gt=0, le=10)
    sl_atr: float | None = Field(None, gt=0, le=20)
    tp_atr: float | None = Field(None, gt=0, le=50)
    timeframe: Literal["1m", "5m", "15m", "1h", "4h"] | None = None
    alerts_enabled: bool | None = None
    alert_threshold: float | None = Field(None, ge=30, le=100)
    signal_threshold: float | None = Field(None, ge=5, le=69)
    assets: dict[str, AssetOverride] | None = None


def previous_session_close(bars_1h: pd.DataFrame, now: datetime) -> float | None:
    """Cierre de la sesión anterior: último cierre antes de las 17:00 de Chicago más reciente (inicio de sesión)."""
    if bars_1h is None or bars_1h.empty:
        return None
    local = pd.Timestamp(now).tz_convert("America/Chicago")
    start = local.normalize() + pd.Timedelta(hours=17)
    if local < start:
        start -= pd.Timedelta(days=1)
    while start.weekday() == 5 or (start.weekday() == 4):  # sábado y viernes 17:00 no abren: la sesión es la del domingo
        start -= pd.Timedelta(days=1)
    before = bars_1h[bars_1h.index < start.tz_convert("UTC")]
    if before.empty:
        return None
    v = float(before["close"].iloc[-1])
    return v if v > 0 else None


class UnknownAsset(KeyError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


class CfdService:
    def __init__(self, Session, feed: CfdFeed, notifier=None, scan_seconds: int = 60, now=_utcnow,
                 open_fn=lambda now: is_open("GLB", now)):
        self.Session = Session
        self.feed = feed
        self.base_assets: dict[str, CfdAsset] = dict(feed.assets)
        self.notifier = notifier
        self.scan_seconds = scan_seconds
        self._now = now
        self._open = open_fn
        self._strong: dict[tuple[str, str], int] = {}   # última dirección fuerte por (activo, temporalidad)
        self.last_scan: datetime | None = None
        self.last_scan_error: str | None = None
        self._task: asyncio.Task | None = None

    # --- estado guardado -----------------------------------------------------------
    def _get(self, key: str) -> dict | None:
        with self.Session() as s:
            row = s.get(CfdState, key)
            return json.loads(row.value) if row else None

    def _put(self, key: str, value: dict | None) -> None:
        with self.Session() as s:
            row = s.get(CfdState, key)
            if value is None:
                if row:
                    s.delete(row)
            elif row:
                row.value, row.updated_at = json.dumps(value), _utcnow()
            else:
                s.add(CfdState(key=key, value=json.dumps(value), updated_at=_utcnow()))
            s.commit()

    def settings(self) -> CfdSettings:
        raw = self._get("settings") or {}
        try:
            return CfdSettings.model_validate(raw)
        except Exception:  # ajustes corruptos o de otra versión: se vuelve a los valores de partida
            log.warning("ajustes CFD inválidos; se usan los de partida")
            return CfdSettings()

    def save_settings(self, patch: SettingsPatch) -> CfdSettings:
        cur = self.settings().model_dump()
        upd = patch.model_dump(exclude_unset=True, exclude_none=True)
        if "assets" in upd:
            unknown = set(upd["assets"]) - set(self.base_assets)
            if unknown:
                raise UnknownAsset(", ".join(sorted(unknown)))
            merged = dict(cur.get("assets") or {})
            for code, ov in upd["assets"].items():
                merged[code] = {**(merged.get(code) or {}), **{k: v for k, v in ov.items() if v is not None}}
            upd["assets"] = merged
        new = CfdSettings.model_validate({**cur, **upd})
        self._put("settings", new.model_dump())
        return new

    def assets(self, st: CfdSettings | None = None) -> dict[str, CfdAsset]:
        st = st or self.settings()
        out = {}
        for code, a in self.base_assets.items():
            ov = st.assets.get(code)
            fields = {k: v for k, v in (ov.model_dump() if ov else {}).items()
                      if v is not None and k in ("value_per_point", "spread_points", "margin_pct", "min_lot", "lot_step", "digits")}
            out[code] = replace(a, **fields) if fields else a
        return out

    def _check(self, code: str) -> str:
        code = code.upper()
        if code not in self.base_assets:
            raise UnknownAsset(code)
        return code

    def calibration(self, code: str) -> Calibration | None:
        raw = self._get(f"calibration:{code}")
        if not raw:
            return None
        return Calibration(raw["asset"], raw["xtb_price"], datetime.fromisoformat(raw["at"]), raw.get("offsets") or {},
                           raw.get("manual_offset"))

    def _save_cal(self, cal: Calibration) -> None:
        self._put(f"calibration:{cal.asset}", {"asset": cal.asset, "xtb_price": cal.xtb_price, "at": cal.at.isoformat(),
                                               "offsets": cal.offsets, "manual_offset": cal.manual_offset})

    def calibrate(self, code: str, xtb_price: float | None = None, at: datetime | None = None,
                  manual_offset: float | None = None) -> dict:
        """Guarda el precio que se ve en xStation (y su hora) o un desfase escrito a mano."""
        code = self._check(code)
        now = self._now()
        if manual_offset is not None:
            if not math.isfinite(manual_offset):
                raise ValueError("el desfase debe ser un número")
            cal = Calibration(code, xtb_price or 0.0, now, {}, manual_offset)
        else:
            if xtb_price is None or not math.isfinite(xtb_price) or xtb_price <= 0:
                raise ValueError("ingresa el precio de xStation (mayor que 0)")
            at = (at or now).astimezone(timezone.utc)
            if at > now + timedelta(minutes=2):
                raise ValueError("la hora de la calibración está en el futuro")
            cal = Calibration(code, float(xtb_price), at)
        self._save_cal(cal)
        self._resolve(cal, self.feed.sources_for(code, "1m"))
        return self.calibration_view(code)

    def clear_calibration(self, code: str) -> None:
        self._put(f"calibration:{self._check(code)}", None)

    def _resolve(self, cal: Calibration, sources: list[str], have: dict[str, pd.DataFrame] | None = None) -> None:
        """Completa los desfases pendientes de cada fuente cuando su vela de 1 minuto ya llegó."""
        if cal.manual_offset is not None:
            return
        changed = False
        for src in sources:
            if src in cal.offsets or "error:" + src in cal.offsets:
                continue
            try:
                bars = (have or {}).get(src)
                if bars is None:
                    bars = self.feed.bars(cal.asset, "1m", source=src).bars
                res = resolve_offset(cal.xtb_price, cal.at, bars)
            except ValueError as e:
                cal.offsets["error:" + src] = {"error": str(e)}
                changed = True
                continue
            except Exception as e:  # la fuente no respondió: se reintenta en la próxima consulta
                log.info("calibración %s/%s pendiente: %s", cal.asset, src, e)
                continue
            if res is not None:
                cal.offsets[src] = res
                changed = True
        if changed:
            self._save_cal(cal)

    def calibration_view(self, code: str, source: str | None = None) -> dict:
        cal = self.calibration(code)
        src = source or self.feed.sources_for(code, "1m")[0]
        view = calibration_status(cal, src, self._now())
        if cal is not None and cal.manual_offset is None and ("error:" + src) in cal.offsets:
            view = {**view, "state": "error", "text": cal.offsets["error:" + src]["error"]}
        return {**view, "source": src, "source_name": SOURCE_NAMES.get(src, src)}

    # --- análisis --------------------------------------------------------------------
    def _fetch_all(self, codes: list[str]) -> dict[tuple[str, str], FeedBars | Exception]:
        jobs = [(c, t) for c in codes for t in FETCH_TFS]
        out: dict[tuple[str, str], FeedBars | Exception] = {}

        def one(job):
            try:
                return job, self.feed.bars(*job)
            except Exception as e:  # cada activo/temporalidad falla por separado
                return job, e

        with ThreadPoolExecutor(max_workers=6) as pool:
            for job, res in pool.map(one, jobs):
                out[job] = res
        for c in codes:  # 4 horas desde 1 hora (ya en caché)
            try:
                out[(c, "4h")] = self.feed.bars(c, "4h")
            except Exception as e:
                out[(c, "4h")] = e
        return out

    def _asof(self, data, code: str, now: datetime, market_open: bool) -> pd.Timestamp:
        """Hora del dato: con el mercado abierto, fin de la última vela de 1 minuto (el feed va atrasado);
        cerrado, la hora actual (todas las velas ya cerraron)."""
        if market_open:
            m1 = data.get((code, "1m"))
            if isinstance(m1, FeedBars) and not m1.bars.empty:
                return m1.bars.index[-1] + TF_DELTA["1m"]
        return pd.Timestamp(now)

    def _analyze(self, data, code: str, now: datetime, market_open: bool) -> dict[str, TfAnalysis]:
        asof = self._asof(data, code, now, market_open)
        out = {}
        for tf in TIMEFRAMES:
            fb = data.get((code, tf))
            if isinstance(fb, FeedBars):
                out[tf] = analyze_tf(fb.bars, tf, asof)
            else:
                out[tf] = TfAnalysis(tf, None, reason=str(fb) if fb is not None else "sin datos")
        return out

    def compute(self, tf: str, codes: list[str] | None = None) -> dict:
        """Escáner completo de la temporalidad `tf` (el filtro intermercado necesita los 6 activos)."""
        if tf not in OPERABLE:
            raise ValueError(f"temporalidad no válida: {tf} (usa {', '.join(OPERABLE)})")
        st = self.settings()
        assets = self.assets(st)
        now = self._now()
        market_open = bool(self._open(now))
        all_codes = list(self.base_assets)
        data = self._fetch_all(all_codes)
        analyses = {c: self._analyze(data, c, now, market_open) for c in all_codes}
        rtf = regime_tf(tf)
        rg = regime({c: analyses[c][rtf].score for c in all_codes}, rtf)
        rows = {}
        for c in codes or all_codes:
            rows[c] = self._row(c, tf, assets[c], data, analyses[c], rg, st, now, market_open)
        return {"tf": tf, "now": now, "market_open": market_open, "regime": rg, "rows": rows, "data": data,
                "settings": st, "analyses": analyses}

    def _row(self, code, tf, a: CfdAsset, data, by_tf: dict[str, TfAnalysis], rg: Regime, st: CfdSettings,
             now: datetime, market_open: bool) -> dict:
        m1 = data.get((code, "1m"))
        price_fb = m1 if isinstance(m1, FeedBars) and not m1.bars.empty else None
        op = data.get((code, tf))
        op_fb = op if isinstance(op, FeedBars) else None
        if price_fb is None and op_fb is not None and not op_fb.bars.empty:
            price_fb = op_fb
        errors = [str(v) for k, v in data.items() if k[0] == code and isinstance(v, Exception)]
        notes = sorted({n for k, v in data.items() if k[0] == code and isinstance(v, FeedBars) for n in v.notes}
                       | {v.error for k, v in data.items() if k[0] == code and isinstance(v, FeedBars) and v.error})

        feed_price = float(price_fb.bars["close"].iloc[-1]) if price_fb else None
        source = price_fb.source if price_fb else None
        last_bar = price_fb.bars.index[-1] if price_fb else None
        delay_min = None
        if price_fb is not None and market_open and price_fb is m1:
            delay_min = max(0.0, (pd.Timestamp(now) - (last_bar + TF_DELTA["1m"])).total_seconds() / 60)

        cal = self.calibration(code)
        if cal is not None and source is not None:
            have = {source: price_fb.bars} if price_fb is m1 else None
            self._resolve(cal, [source], have)
        offset = cal.offset_for(source) if (cal is not None and source is not None) else None
        cal_view = self.calibration_view(code, source) if source else calibration_status(cal, "yahoo", now)

        # Cambio del día: contra el cierre de la sesión anterior de CME (abre 17:00 hora de Chicago), con velas de
        # 1 hora. La vela diaria de Yahoo para futuros no sirve: la sesión en curso a veces reemplaza a la anterior.
        change_pct = None
        h1 = data.get((code, "1h"))
        if isinstance(h1, FeedBars) and feed_price is not None:
            prev = previous_session_close(h1.bars, now)
            if prev:
                change_pct = (feed_price / prev - 1) * 100

        base, rules = composite(by_tf, tf)
        adj = intermarket(code, base, rg) if base is not None else []
        # Entero antes del veredicto: el número que se muestra y la etiqueta siempre coinciden.
        final = float(round(clip(base + sum(r.points for r in adj)))) if base is not None else None
        label = verdict(final, st.alert_threshold, st.signal_threshold)
        sign = 0 if final is None else (1 if final > 0 else -1 if final < 0 else 0)
        rules_out = []
        for r in rules + adj:
            d = r.to_dict()
            d["agrees"] = r.available and sign != 0 and ((r.value > 0) == (sign > 0)) and r.value != 0
            rules_out.append(d)

        op_a = by_tf.get(tf)
        atr = op_a.last.get("atr14") if op_a and op_a.last else None
        ticket = build_ticket(TicketInput(sign, feed_price or 0.0, atr, offset, st.capital_usd, st.risk_pct,
                                          st.sl_atr, st.tp_atr), a) if feed_price else {
            "ok": False, "error": "Sin precio del feed.", "warnings": []}
        ticket["actionable"] = label not in ("NEUTRAL", "SIN DATOS") and ticket.get("ok", False)

        return {
            "code": code, "name": a.name, "group": a.group, "tf": tf, "tf_label": TF_LABEL[tf],
            "feed_symbol": a.twelvedata if source == "twelvedata" else a.yahoo,
            "source": source, "source_name": SOURCE_NAMES.get(source, source) if source else None,
            "feed_price": feed_price, "offset": offset,
            "xtb_price": round(feed_price + offset, a.digits) if (feed_price is not None and offset is not None) else None,
            "digits": a.digits, "change_pct": change_pct,
            "last_bar": last_bar.isoformat() if last_bar is not None else None,
            "delay_min": None if delay_min is None else round(delay_min, 1),
            "market_open": market_open,
            "score": None if final is None else round(final, 1),
            "base_score": None if base is None else round(base, 1),
            "verdict": label,
            "candle_time": op_a.candle_time.isoformat() if op_a and op_a.candle_time is not None else None,
            "atr": atr,
            "atr_pct": (atr / feed_price * 100) if (atr and feed_price) else None,
            "bias": {t: {"score": None if by_tf[t].score is None else round(by_tf[t].score, 1), "bias": by_tf[t].bias,
                         "reason": by_tf[t].reason} for t in TIMEFRAMES},
            "rules": rules_out,
            "ticket": ticket,
            "calibration": cal_view,
            "errors": errors,
            "notes": notes,
            "reason": None if final is not None else (op_a.reason if op_a and op_a.reason else "sin datos de la temporalidad"),
        }

    def overview(self, tf: str | None = None) -> dict:
        st = self.settings()
        res = self.compute(tf or st.timeframe)
        return {
            "disclaimer": DISCLAIMER,
            "tf": res["tf"], "tf_label": TF_LABEL[res["tf"]],
            "now": res["now"].isoformat(), "market_open": res["market_open"],
            "regime": res["regime"].to_dict(),
            "assets": [self._public_row(r) for r in res["rows"].values()],
            "settings": res["settings"].model_dump(),
            "contracts": {c: self._contract(a) for c, a in self.assets(st).items()},
            "feed": self.feed.status(),
            "alerts": {"telegram": bool(self.notifier and self.notifier.configured().get("telegram")),
                       "last_scan": self.last_scan.isoformat() if self.last_scan else None,
                       "last_scan_error": self.last_scan_error},
            "timeframes": OPERABLE,
        }

    @staticmethod
    def _contract(a: CfdAsset) -> dict:
        return {"value_per_point": a.value_per_point, "min_lot": a.min_lot, "lot_step": a.lot_step,
                "digits": a.digits, "margin_pct": a.margin_pct, "spread_points": a.spread_points,
                "yahoo": a.yahoo, "twelvedata": a.twelvedata}

    @staticmethod
    def _public_row(r: dict) -> dict:
        return {k: v for k, v in r.items() if k != "rules"}

    def detail(self, code: str, tf: str | None = None) -> dict:
        code = self._check(code)
        st = self.settings()
        tf = tf or st.timeframe
        res = self.compute(tf, [code])
        row = res["rows"][code]
        fb = res["data"].get((code, tf))
        bars = []
        chart_offset = None
        if isinstance(fb, FeedBars) and not fb.bars.empty:
            cal = self.calibration(code)
            chart_offset = cal.offset_for(fb.source) if cal else None
            df = with_indicators(fb.bars).iloc[-CHART_BARS:]
            sh = chart_offset or 0.0
            for ts, r in df.iterrows():
                bars.append({"t": int(ts.timestamp()), **{k: (None if _f(r[k]) is None else round(_f(r[k]) + sh, 6))
                                                          for k in ("open", "high", "low", "close", "ema20", "ema50", "ema200")}})
        return {
            "disclaimer": DISCLAIMER, **row, "regime": res["regime"].to_dict(),
            "contract": self._contract(self.assets(st)[code]),
            "chart": {"bars": bars, "source": fb.source if isinstance(fb, FeedBars) else None,
                      "calibrated": chart_offset is not None,
                      "forming_from": row["candle_time"]},
            "mtf": [{"tf": t, "weight": w, "label": TF_LABEL[t]} for t, w in mtf_plan(tf)],
        }

    # --- alertas --------------------------------------------------------------------
    def scan(self) -> list[dict]:
        """Revisa los 6 activos en la temporalidad elegida y avisa por Telegram las señales fuertes nuevas.
        Una alerta por activo, temporalidad, vela y dirección; y solo cuando la señal ENTRA en zona fuerte
        (si se mantiene fuerte en las velas siguientes no se repite)."""
        st = self.settings()
        now = self._now()
        if not st.alerts_enabled or not self._open(now):
            return []
        res = self.compute(st.timeframe)
        created = []
        for code, row in res["rows"].items():
            score = row["score"]
            k = (code, st.timeframe)
            strong = 0 if score is None else 1 if score >= st.alert_threshold else -1 if score <= -st.alert_threshold else 0
            prev = self._strong.get(k)
            self._strong[k] = strong
            if strong == 0 or prev == strong or row["candle_time"] is None:
                continue
            key = f"cfd:{code}:{st.timeframe}:{row['candle_time']}:{'+' if strong > 0 else '-'}"
            with self.Session() as s:
                if s.scalar(select(func.count()).select_from(AlertRow).where(AlertRow.key == key)):
                    continue
            created.append(self._alert(key, row))
        self.last_scan = now
        return created

    def _alert(self, key: str, row: dict) -> dict:
        t = row["ticket"]
        d = row["digits"]
        icon = "🟢" if (row["score"] or 0) > 0 else "🔴"
        title = f"{icon} CFD {row['code']} {row['verdict']} ({row['tf_label']}) · puntaje {row['score']:+.0f}"
        lines = []
        if t.get("entry") is not None:
            lines.append(f"{t['side']} · entrada ≈ {t['entry']:.{d}f} · SL {t['sl']:.{d}f} · TP {t['tp']:.{d}f}")
            lines.append(f"Stop a {t['sl_points']:.{d}f} pts · objetivo a {t['tp_points']:.{d}f} pts · R:R {t['rr']:.2f}")
            if t.get("ok"):
                lines.append(f"Lotes {t['lots']:.2f} · riesgo {t['risk_usd']:.2f} USD · objetivo {t['reward_usd']:.2f} USD")
            elif t.get("error"):
                lines.append(t["error"])
        if not t.get("calibrated"):
            lines.append("⚠ Sin calibrar: precios del feed, no de xStation.")
        if row.get("delay_min") is not None:
            lines.append(f"Datos de {row['source_name']} con ~{row['delay_min']:.0f} min de retraso: revisa el precio en xStation.")
        lines.append("El copiloto no opera: decide y ejecuta tú en xStation.")
        message = "\n".join(lines)
        alert = AlertRow(key=key, kind="cfd", severity="warning", symbol=None, title=title, message=message,
                         value=row["score"], created_at=_utcnow())
        if self.notifier is not None:
            sent, errors = self.notifier.send(title, message)
            alert.notified = ",".join(sent) or None
            alert.notify_error = "; ".join(errors) or None
        with self.Session() as s:
            s.add(alert)
            s.commit()
            s.refresh(alert)
        log.info("alerta CFD %s", title)
        return {"id": alert.id, "key": key, "title": title, "message": message, "notified": alert.notified}

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.scan)
                self.last_scan_error = None
            except Exception as e:  # el escáner no debe botar el servidor
                log.warning("escáner CFD: %s", e)
                self.last_scan_error = str(e)
            await asyncio.sleep(self.scan_seconds)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
