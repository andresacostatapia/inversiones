"""Punto de entrada de la API.

    cd analisis/backend
    uvicorn app.main:app --reload            # http://127.0.0.1:8000 (dashboard) y /docs (API)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import DISCLAIMER, router
from .config import ROOT_DIR, AppConfig, get_config, get_secrets
from .db import make_engine, make_session_factory
from .market_data import MarketData
from .providers.fx import FxService, build_fx_service
from .providers.router import ProviderRouter, build_router
from .alerts import AlertEngine, Notifier
from .portfolio_service import PortfolioService
from .scheduler import Poller
from .signal_service import SignalService

def frontend_dist() -> Path:
    """Dashboard compilado (cd analisis/frontend && npm run build). Si existe, se sirve en la raíz.
    La app de escritorio lo trae dentro del paquete e indica su ruta con ANALISIS_FRONTEND_DIST."""
    return Path(os.environ.get("ANALISIS_FRONTEND_DIST") or ROOT_DIR / "frontend" / "dist")


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(
    cfg: AppConfig | None = None,
    provider_router: ProviderRouter | None = None,
    fx: FxService | None = None,
    db_path: str | None = None,
    start_poller: bool = True,
    notifier: Notifier | None = None,
) -> FastAPI:
    cfg = cfg or get_config()
    provider_router = provider_router or build_router(cfg, get_secrets())
    yf = provider_router.providers.get("yfinance")
    fx = fx or build_fx_service(cfg.data.fx.sources, yf)
    engine = make_engine(db_path or cfg.database_path)
    md = MarketData(cfg, make_session_factory(engine), provider_router, fx)
    md.startup()
    signals = SignalService(md)
    portfolio = PortfolioService(md, signals)
    notifier = notifier or Notifier(get_secrets(), cfg.alerts.channels)
    alerts = AlertEngine(md, portfolio, notifier, cfg)
    poller = Poller(md, signals, alerts)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_poller:
            poller.start()
        yield
        await poller.stop()

    app = FastAPI(title="Análisis de acciones y ETF", description=DISCLAIMER, lifespan=lifespan)
    app.state.md, app.state.poller, app.state.signals = md, poller, signals
    app.state.portfolio, app.state.alerts = portfolio, alerts
    # El dashboard (fase 3) corre en otro puerto durante el desarrollo.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    dist = frontend_dist()
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    return app


def __getattr__(name):  # `uvicorn app.main:app` crea la app solo cuando se pide
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
