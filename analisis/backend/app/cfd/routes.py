"""Rutas REST del copiloto CFD (/api/cfd/…)."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from .assets import OPERABLE
from .service import CfdService, SettingsPatch, UnknownAsset

router = APIRouter(prefix="/api/cfd")


def svc(request: Request) -> CfdService:
    s = getattr(request.app.state, "cfd", None)
    if s is None:
        raise HTTPException(404, "El copiloto CFD está desactivado (cfd.enabled en config.yaml)")
    return s


def _tf(tf: str | None) -> str | None:
    if tf is not None and tf not in OPERABLE:
        raise HTTPException(422, f"Temporalidad no válida: {tf}. Usa {', '.join(OPERABLE)}.")
    return tf


class CalibrateIn(BaseModel):
    asset: str
    xtb_price: float | None = None
    at: datetime | None = None          # hora en que se leyó el precio (por defecto, ahora)
    manual_offset: float | None = None  # alternativa: desfase escrito a mano


@router.get("/overview")
async def overview(request: Request, tf: str | None = Query(None)):
    return await asyncio.to_thread(svc(request).overview, _tf(tf))


@router.get("/assets/{code}")
async def detail(code: str, request: Request, tf: str | None = Query(None)):
    try:
        return await asyncio.to_thread(svc(request).detail, code, _tf(tf))
    except UnknownAsset:
        raise HTTPException(404, f"Activo desconocido: {code}. Usa GOLD, SILVER, US500, US100, US30 o US2000.")


@router.get("/settings")
def get_settings(request: Request):
    return svc(request).settings().model_dump()


@router.put("/settings")
def put_settings(body: SettingsPatch, request: Request):
    try:
        return svc(request).save_settings(body).model_dump()
    except UnknownAsset as e:
        raise HTTPException(422, f"Activo desconocido: {e}")
    except ValidationError as e:
        raise HTTPException(422, "; ".join(err["msg"] for err in e.errors()))


@router.post("/calibrate")
async def calibrate(body: CalibrateIn, request: Request):
    try:
        return await asyncio.to_thread(svc(request).calibrate, body.asset, body.xtb_price, body.at, body.manual_offset)
    except UnknownAsset:
        raise HTTPException(404, f"Activo desconocido: {body.asset}")
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.delete("/calibrate/{code}")
def clear_calibration(code: str, request: Request):
    try:
        svc(request).clear_calibration(code)
    except UnknownAsset:
        raise HTTPException(404, f"Activo desconocido: {code}")
    return {"cleared": code.upper()}


@router.post("/scan")
async def scan_now(request: Request):
    """Revisa ahora las alertas (respeta si están activadas y si el mercado está abierto)."""
    s = svc(request)
    created = await asyncio.to_thread(s.scan)
    return {"created": created, "alerts_enabled": s.settings().alerts_enabled}
