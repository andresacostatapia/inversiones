"""Punto de entrada de la API.

    cd analisis/backend
    uvicorn app.main:app --reload            # http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import DISCLAIMER, router
from .config import AppConfig, get_config, get_secrets
from .db import make_engine, make_session_factory
from .market_data import MarketData
from .providers.fx import FxService, build_fx_service
from .providers.router import ProviderRouter, build_router
from .scheduler import Poller
from .signal_service import SignalService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(
    cfg: AppConfig | None = None,
    provider_router: ProviderRouter | None = None,
    fx: FxService | None = None,
    db_path: str | None = None,
    start_poller: bool = True,
) -> FastAPI:
    cfg = cfg or get_config()
    provider_router = provider_router or build_router(cfg, get_secrets())
    yf = provider_router.providers.get("yfinance")
    fx = fx or build_fx_service(cfg.data.fx.sources, yf)
    engine = make_engine(db_path or cfg.database_path)
    md = MarketData(cfg, make_session_factory(engine), provider_router, fx)
    md.seed_watchlist()
    signals = SignalService(md)
    poller = Poller(md, signals)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_poller:
            poller.start()
        yield
        await poller.stop()

    app = FastAPI(title="Análisis de acciones y ETF", description=DISCLAIMER, lifespan=lifespan)
    app.state.md, app.state.poller, app.state.signals = md, poller, signals
    # El dashboard (fase 3) corre en otro puerto durante el desarrollo.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


def __getattr__(name):  # `uvicorn app.main:app` crea la app solo cuando se pide
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
