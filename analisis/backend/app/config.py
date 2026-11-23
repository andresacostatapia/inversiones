"""Configuración: config.yaml (perfil, watchlist, proveedores) + .env (API keys)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# analisis/  (backend/app/config.py -> parents[2])
ROOT_DIR = Path(__file__).resolve().parents[2]


class Secrets(BaseSettings):
    """API keys y credenciales. Vienen del entorno o de analisis/.env; nunca de config.yaml."""

    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    finnhub_api_key: str = ""
    twelvedata_api_key: str = ""
    alphavantage_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email_to: str = ""


class RateLimit(BaseModel):
    per_minute: int | None = None
    per_day: int | None = None


class ProviderOrder(BaseModel):
    quotes: dict[str, list[str]] = Field(default_factory=lambda: {"CL": ["yfinance"], "US": ["yfinance"]})
    history: dict[str, list[str]] = Field(default_factory=lambda: {"CL": ["yfinance"], "US": ["yfinance"]})


class FxConfig(BaseModel):
    sources: list[str] = Field(default_factory=lambda: ["mindicador", "yfinance"])


class DataConfig(BaseModel):
    poll_seconds: int = 60
    closed_poll_seconds: int = 3600
    history_years: int = 10
    stale_after_minutes: int = 30
    providers: ProviderOrder = Field(default_factory=ProviderOrder)
    rate_limits: dict[str, RateLimit] = Field(default_factory=dict)
    fx: FxConfig = Field(default_factory=FxConfig)


class GeneralConfig(BaseModel):
    timezone: str = "America/Santiago"
    base_currency: str = "CLP"
    database: str = "data/analisis.db"


class AppConfig(BaseModel):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    watchlist: list[str] = Field(default_factory=list)
    data: DataConfig = Field(default_factory=DataConfig)
    # Secciones de fases posteriores: se guardan tal cual y se validan en su fase.
    signals: dict[str, Any] = Field(default_factory=dict)
    backtest: dict[str, Any] = Field(default_factory=dict)
    risk_profile: dict[str, Any] = Field(default_factory=dict)

    @property
    def database_path(self) -> Path:
        p = Path(self.general.database)
        return p if p.is_absolute() else ROOT_DIR / p


def load_config(path: Path | str | None = None) -> AppConfig:
    path = Path(path or os.environ.get("ANALISIS_CONFIG", ROOT_DIR / "config.yaml"))
    if not path.exists():
        return AppConfig()
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)


@lru_cache
def get_config() -> AppConfig:
    return load_config()


@lru_cache
def get_secrets() -> Secrets:
    return Secrets()
