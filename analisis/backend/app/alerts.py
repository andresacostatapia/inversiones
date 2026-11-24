"""Alertas: caída desde la compra (−20 % revisar / −25 % vender), cambios de señal y fallas de datos.

Dos tipos:
- **De condición** (caídas, datos): se crean una vez mientras la condición se cumple y se marcan "resueltas"
  cuando deja de cumplirse. Si vuelve a cumplirse, se crea una nueva. Así no se repiten cada minuto.
- **De evento** (cambio de señal): una por símbolo, día y etiqueta.

Siempre se guardan para la UI. Además se envían por Telegram y/o email si hay credenciales en .env y la gravedad
alcanza `alerts.notify_min_severity`.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx
from sqlalchemy import func, select, update

from .config import AppConfig, Secrets
from .db import AlertRow, LatestQuote, SignalRow
from .market_data import MarketData, as_utc
from .market_hours import is_open
from .portfolio import InvalidTransaction
from .providers.base import market_of, utcnow
from .textfmt import num, pct

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
FOOTER = "Herramienta educativa, no es asesoría financiera."


class Notifier:
    """Envía alertas fuera de la app. Un canal sin credenciales simplemente no se usa."""

    def __init__(self, secrets: Secrets, channels: list[str], http: httpx.Client | None = None, smtp_factory=None):
        self.s = secrets
        self.channels = channels
        self._http = http
        self._smtp_factory = smtp_factory or smtplib.SMTP

    def configured(self) -> dict[str, bool]:
        return {
            "telegram": "telegram" in self.channels and bool(self.s.telegram_bot_token and self.s.telegram_chat_id),
            "email": "email" in self.channels and bool(self.s.smtp_host and self.s.alert_email_to),
        }

    def _telegram(self, title: str, message: str) -> None:
        http = self._http or httpx.Client(timeout=10)
        r = http.post(f"https://api.telegram.org/bot{self.s.telegram_bot_token}/sendMessage",
                      json={"chat_id": self.s.telegram_chat_id, "text": f"{title}\n{message}\n\n{FOOTER}",
                            "disable_web_page_preview": True})
        if r.status_code != 200:
            raise RuntimeError(f"Telegram respondió {r.status_code}")

    def _email(self, title: str, message: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[Análisis] {title}"
        msg["From"] = self.s.smtp_user or self.s.alert_email_to
        msg["To"] = self.s.alert_email_to
        msg.set_content(f"{message}\n\n{FOOTER}")
        with self._smtp_factory(self.s.smtp_host, self.s.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if self.s.smtp_user:
                smtp.login(self.s.smtp_user, self.s.smtp_password)
            smtp.send_message(msg)

    def send(self, title: str, message: str) -> tuple[list[str], list[str]]:
        sent, errors = [], []
        for ch, ok in self.configured().items():
            if not ok:
                continue
            try:
                (self._telegram if ch == "telegram" else self._email)(title, message)
                sent.append(ch)
            except Exception as e:  # un canal caído no debe impedir guardar la alerta
                log.warning("no se pudo enviar por %s: %s", ch, e)
                errors.append(f"{ch}: {e}")
        return sent, errors


class AlertEngine:
    def __init__(self, md: MarketData, portfolio, notifier: Notifier, cfg: AppConfig):
        self.md = md
        self.portfolio = portfolio
        self.notifier = notifier
        self.cfg = cfg

    # --- almacenamiento ---------------------------------------------------------
    def _active(self, key: str) -> AlertRow | None:
        with self.md.Session() as s:
            return s.scalars(select(AlertRow).where(AlertRow.key == key, AlertRow.resolved_at.is_(None))
                             .order_by(AlertRow.id.desc()).limit(1)).first()

    def _exists(self, key: str) -> bool:
        with self.md.Session() as s:
            return s.scalar(select(func.count()).select_from(AlertRow).where(AlertRow.key == key)) > 0

    def _create(self, key: str, kind: str, severity: str, symbol: str | None, title: str, message: str,
                value: float | None = None) -> dict:
        row = AlertRow(key=key, kind=kind, severity=severity, symbol=symbol, title=title, message=message,
                       value=value, created_at=utcnow())
        if SEVERITY_ORDER[severity] >= SEVERITY_ORDER.get(self.cfg.alerts.notify_min_severity, 1):
            sent, errors = self.notifier.send(title, message)
            row.notified = ",".join(sent) or None
            row.notify_error = "; ".join(errors) or None
        with self.md.Session() as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        log.info("alerta [%s] %s", severity, title)
        return _alert_dict(row)

    def _resolve(self, key: str) -> None:
        with self.md.Session() as s:
            s.execute(update(AlertRow).where(AlertRow.key == key, AlertRow.resolved_at.is_(None))
                      .values(resolved_at=utcnow()))
            s.commit()

    def _condition(self, active: bool, key: str, **kw) -> dict | None:
        if active and self._active(key) is None:
            return self._create(key, **kw)
        if not active and self._active(key) is not None:
            self._resolve(key)
        return None

    # --- reglas -------------------------------------------------------------------
    def _drawdowns(self) -> list[dict]:
        rp = self.cfg.risk_profile
        try:
            pf = self.portfolio.portfolio()
        except InvalidTransaction as e:
            log.warning("cartera inconsistente: %s", e)
            return []
        new = []
        held = set()
        for p in pf["positions"]:
            sym, dd = p["symbol"], p["drawdown_pct"]
            held.add(sym)
            if dd is None:
                continue
            cur = p["currency"]
            base = (f"{sym} está {pct(dd)} bajo tu costo promedio de compra ({num(p['avg_cost'])} {cur}; "
                    f"precio actual {num(p['price'])} {cur}).")
            status = p["status"]  # misma regla que la tabla de la cartera
            sell = status == "vender"
            review = status in ("revisar", "vender")
            a = self._condition(sell, f"venta:{sym}", kind="venta", severity="critical", symbol=sym,
                                title=f"{sym}: caída de {pct(dd)} — umbral de VENTA ({pct(rp.sell_drawdown_pct, 0)})",
                                message=base + " Tu perfil de riesgo indica considerar vender.", value=dd)
            b = self._condition(review and not sell, f"revision:{sym}", kind="revision", severity="warning", symbol=sym,
                                title=f"{sym}: caída de {pct(dd)} — revisar la posición ({pct(rp.review_drawdown_pct, 0)})",
                                message=base + " Revisa si la tesis de inversión sigue en pie.", value=dd)
            new += [x for x in (a, b) if x]
        # Posiciones vendidas por completo: sus alertas de caída ya no aplican.
        with self.md.Session() as s:
            open_keys = s.scalars(select(AlertRow.key).where(AlertRow.resolved_at.is_(None),
                                                             AlertRow.kind.in_(("venta", "revision")))).all()
        for k in open_keys:
            if k.split(":", 1)[1] not in held:
                self._resolve(k)
        return new

    def _signal_changes(self) -> list[dict]:
        if not self.cfg.alerts.signal_changes:
            return []
        new = []
        for sym in self.md.watchlist():
            with self.md.Session() as s:
                last2 = s.scalars(select(SignalRow).where(SignalRow.symbol == sym)
                                  .order_by(SignalRow.date.desc()).limit(2)).all()
            if len(last2) < 2 or not last2[0].label or not last2[1].label or last2[0].label == last2[1].label:
                continue
            cur, prev = last2
            key = f"senal:{sym}:{cur.date.isoformat()}:{cur.label}"
            if self._exists(key):
                continue
            sev = "info" if cur.label == "MANTENER" else "warning"
            prov = " (provisional: el mercado sigue abierto)" if cur.provisional else ""
            new.append(self._create(
                key, kind="senal", severity=sev, symbol=sym,
                title=f"{sym}: la señal cambió de {prev.label} a {cur.label}{prov}",
                message=f"{cur.summary or ''} Puntaje {num(cur.score, 0)}, confianza {num(cur.confidence, 0)} %.",
                value=cur.score,
            ))
        return new

    def _data_errors(self, now: datetime) -> list[dict]:
        limit = timedelta(minutes=self.cfg.alerts.data_error_minutes)
        new = []
        for sym in self.md.watchlist():
            with self.md.Session() as s:
                q = s.get(LatestQuote, sym)
            # fetched_at solo cambia cuando un precio llega bien: es la hora del último dato bueno.
            last_ok = as_utc(q.fetched_at) if q else None
            failing = bool(q and q.status != "ok" and is_open(market_of(sym), now)
                           and (last_ok is None or now - last_ok >= limit))
            a = self._condition(failing, f"datos:{sym}", kind="datos", severity="info", symbol=sym,
                                title=f"{sym}: sin precios nuevos desde hace más de {self.cfg.alerts.data_error_minutes} min",
                                message=f"El proveedor falla: {q.error if q else ''}. Se muestra el último precio conocido.")
            if a:
                new.append(a)
        return new

    def evaluate(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        return self._drawdowns() + self._signal_changes() + self._data_errors(now)

    # --- consultas para la API ------------------------------------------------------
    def list(self, unread_only: bool = False, limit: int = 100) -> list[dict]:
        with self.md.Session() as s:
            q = select(AlertRow).order_by(AlertRow.id.desc()).limit(limit)
            if unread_only:
                q = q.where(AlertRow.read_at.is_(None))
            return [_alert_dict(r) for r in s.scalars(q).all()]

    def counts(self) -> dict:
        with self.md.Session() as s:
            rows = s.execute(select(AlertRow.severity, func.count()).where(AlertRow.read_at.is_(None))
                             .group_by(AlertRow.severity)).all()
        by = {k: v for k, v in rows}
        return {"unread": sum(by.values()), "by_severity": by}

    def mark_read(self, alert_id: int | None = None) -> int:
        with self.md.Session() as s:
            q = update(AlertRow).where(AlertRow.read_at.is_(None))
            if alert_id is not None:
                q = q.where(AlertRow.id == alert_id)
            n = s.execute(q.values(read_at=utcnow())).rowcount
            s.commit()
        return n

    def test(self) -> dict:
        """Alerta de prueba: siempre se guarda y se intenta enviar por los canales configurados."""
        sent, errors = self.notifier.send("Alerta de prueba", "Si ves este mensaje, las notificaciones funcionan.")
        row = AlertRow(key=f"prueba:{utcnow().isoformat()}", kind="prueba", severity="info", symbol=None,
                       title="Alerta de prueba", message="Prueba de notificaciones.", created_at=utcnow(),
                       notified=",".join(sent) or None, notify_error="; ".join(errors) or None)
        with self.md.Session() as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        return {"alert": _alert_dict(row), "channels": self.notifier.configured(), "sent": sent, "errors": errors}


def _alert_dict(r: AlertRow) -> dict:
    return {
        "id": r.id, "kind": r.kind, "severity": r.severity, "symbol": r.symbol, "title": r.title,
        "message": r.message, "value": r.value,
        "created_at": as_utc(r.created_at).isoformat() if r.created_at else None,
        "resolved_at": as_utc(r.resolved_at).isoformat() if r.resolved_at else None,
        "read": r.read_at is not None, "notified": r.notified.split(",") if r.notified else [],
        "notify_error": r.notify_error,
    }
