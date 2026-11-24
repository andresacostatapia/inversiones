"""
notifier.py — Alertas opcionales por Telegram.

Configura en .env:
    TELEGRAM_BOT_TOKEN=123456:ABC...
    TELEGRAM_CHAT_ID=123456789

Se envía una alerta cuando un activo supera el umbral (score >= +umbral o <= −umbral).
Para no repetir la misma alerta en cada refresco, se registra la combinación
(activo, dirección, vela de la temporalidad operativa) en .state/alerts_sent.json.
"""

from __future__ import annotations

import html
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

import config

load_dotenv(config.BASE_DIR / ".env")
logger = logging.getLogger("cdfplus.notifier")


@dataclass
class SendResult:
    ok: bool
    message: str


class TelegramNotifier:
    """Cliente mínimo de la Bot API de Telegram."""

    API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self.token = (token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")).strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.token) and bool(self.chat_id)

    def send(self, text: str) -> SendResult:
        """Envía un mensaje en HTML. Nunca lanza excepciones: devuelve SendResult."""
        if not self.is_configured:
            return SendResult(False, "Telegram no configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        try:
            resp = requests.post(
                self.API_URL.format(token=self.token),
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=config.TELEGRAM_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            return SendResult(False, f"Error de red con Telegram: {exc.__class__.__name__}")
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        if resp.status_code == 200 and payload.get("ok"):
            return SendResult(True, "Mensaje enviado")
        desc = payload.get("description", f"HTTP {resp.status_code}")
        return SendResult(False, f"Telegram rechazó el mensaje: {desc}")


class AlertDeduplicator:
    """Recuerda qué alertas ya se enviaron para no duplicarlas en la misma vela."""

    def __init__(self, path: Path = config.ALERTS_FILE, retention_days: int = 7) -> None:
        self.path = path
        self.retention = timedelta(days=retention_days)
        self._lock = threading.Lock()
        self._sent: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._sent, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("No se pudo guardar el registro de alertas: %s", exc)

    def _prune(self) -> None:
        limit = datetime.now(timezone.utc) - self.retention
        for k, v in list(self._sent.items()):
            try:
                if datetime.fromisoformat(v) < limit:
                    del self._sent[k]
            except ValueError:
                del self._sent[k]

    @staticmethod
    def make_key(asset: str, direction: int, bar_time: object) -> str:
        return f"{asset}|{'BUY' if direction > 0 else 'SELL'}|{bar_time}"

    def already_sent(self, key: str) -> bool:
        return key in self._sent

    def mark_sent(self, key: str) -> None:
        with self._lock:
            self._sent[key] = datetime.now(timezone.utc).isoformat()
            self._prune()
            self._save()


def format_signal_alert(asset_key: str, asset_name: str, score: float, verdict: str,
                        ticket_lines: list[str], intermarket: str, biases: dict[str, str]) -> str:
    """Construye el texto HTML de una alerta."""
    emoji = "🟢" if score > 0 else "🔴"
    bias_txt = " · ".join(f"{tf}: {b}" for tf, b in biases.items())
    lines = [
        f"{emoji} <b>{html.escape(config.APP_NAME)} · {html.escape(asset_key)}</b> ({html.escape(asset_name)})",
        f"<b>{html.escape(verdict)}</b> · Score <b>{score:+.0f}</b>",
        f"Sesgo: {html.escape(bias_txt)}",
        f"Intermercado: {html.escape(intermarket)}",
        "",
        *[html.escape(x) for x in ticket_lines],
        "",
        "<i>Señal de apoyo a la decisión. Verifica en xStation antes de ejecutar.</i>",
    ]
    return "\n".join(lines)


def should_alert(score: float, threshold: float) -> int:
    """+1 si supera el umbral alcista, −1 si supera el bajista, 0 si no hay alerta."""
    if threshold <= 0:
        return 0
    if score >= threshold:
        return 1
    if score <= -threshold:
        return -1
    return 0
