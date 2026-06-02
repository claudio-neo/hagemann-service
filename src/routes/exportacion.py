"""
Exportación — Hagemann
Endpoints para generación de reportes Excel y preview JSON.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
from uuid import UUID
import io

from ..database import get_db
from ..services.excel_export import (
    generar_reporte_mensual,
    preview_reporte_mensual,
    generar_reporte_rango,
    preview_reporte_rango,
)
from ..auth import require_permission
from ..permisos import EXPORT_RUN, scoped_empleado_ids

router = APIRouter(
    prefix="/exportacion",
    tags=["Exportación"],
    dependencies=[Depends(require_permission(EXPORT_RUN))],
)


def _scope_ids(_auth, db, requested):
    """Limita los empleados exportables al ámbito del usuario (Gruppenadmin → sus grupos)."""
    scoped = scoped_empleado_ids(_auth, db)
    if scoped is None:
        return requested  # Admin / Personalabteilung → sin restricción
    if requested:
        permitidos = set(scoped)
        return [i for i in requested if i in permitidos]
    return scoped


# ========== SCHEMAS ==========

class ExcelRequest(BaseModel):
    year: int
    month: int
    empleado_ids: Optional[List[UUID]] = None


class RangoRequest(BaseModel):
    fecha_von: date
    fecha_bis: date
    empleado_ids: Optional[List[UUID]] = None


# ========== ENDPOINTS ==========

@router.post("/excel")
def exportar_excel(
    body: ExcelRequest,
    db: Session = Depends(get_db),
    _auth=Depends(require_permission(EXPORT_RUN)),
):
    """
    Genera y descarga el reporte mensual de saldos de horas en Excel (.xlsx).
    """
    if not 1 <= body.month <= 12:
        raise HTTPException(400, "Der Monat muss zwischen 1 und 12 liegen")
    if not 2020 <= body.year <= 2099:
        raise HTTPException(400, "Das Jahr muss zwischen 2020 und 2099 liegen")

    try:
        xlsx_bytes = generar_reporte_mensual(
            db,
            year=body.year,
            month=body.month,
            empleado_ids=_scope_ids(_auth, db, body.empleado_ids),
        )
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    filename = f"hagemann_horas_{body.year}-{body.month:02d}.xlsx"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.get("/excel/preview")
def preview_excel(
    year: int = Query(..., description="Año (ej: 2026)"),
    month: int = Query(..., ge=1, le=12, description="Mes 1-12"),
    db: Session = Depends(get_db),
):
    """Preview JSON de los datos del reporte mensual."""
    return preview_reporte_mensual(db, year=year, month=month)


# ── Export por rango de fechas (von – bis) ──────────────────────────────────

@router.post("/excel/rango")
def exportar_excel_rango(
    body: RangoRequest,
    db: Session = Depends(get_db),
    _auth=Depends(require_permission(EXPORT_RUN)),
):
    """
    Genera y descarga el reporte por rango de fechas (von-bis) en Excel.
    Dos hojas: Zusammenfassung (resumen por empleado) + Details (fichajes).
    """
    if body.fecha_bis < body.fecha_von:
        raise HTTPException(400, "Das Bis-Datum darf nicht vor dem Von-Datum liegen")

    try:
        xlsx_bytes = generar_reporte_rango(
            db,
            fecha_von=body.fecha_von,
            fecha_bis=body.fecha_bis,
            empleado_ids=_scope_ids(_auth, db, body.empleado_ids),
        )
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    filename = (
        f"hagemann_horas_{body.fecha_von.isoformat()}_bis_{body.fecha_bis.isoformat()}.xlsx"
    )
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.get("/excel/rango/preview")
def preview_excel_rango(
    fecha_von: date = Query(..., description="Fecha desde (YYYY-MM-DD)"),
    fecha_bis: date = Query(..., description="Fecha hasta (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
):
    """Preview JSON del reporte por rango de fechas."""
    if fecha_bis < fecha_von:
        raise HTTPException(400, "Das Bis-Datum darf nicht vor dem Von-Datum liegen")
    return preview_reporte_rango(db, fecha_von=fecha_von, fecha_bis=fecha_bis)
