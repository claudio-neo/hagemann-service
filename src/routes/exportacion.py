"""
Exportación — Hagemann
Endpoints para generación de reportes Excel y preview JSON.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
import io

from ..database import get_db
from ..services.excel_export import generar_reporte_mensual, preview_reporte_mensual

router = APIRouter(prefix="/exportacion", tags=["Exportación"])


# ========== SCHEMAS ==========

class ExcelRequest(BaseModel):
    year: int
    month: int
    empleado_ids: Optional[List[UUID]] = None


# ========== ENDPOINTS ==========

@router.post("/excel")
def exportar_excel(
    body: ExcelRequest,
    db: Session = Depends(get_db),
):
    """
    Genera y descarga el reporte mensual de horas en formato Excel (.xlsx).

    - **year**: Año (ej: 2026)
    - **month**: Mes 1-12
    - **empleado_ids**: Lista de UUIDs a incluir (opcional; si omite, incluye todos)
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
            empleado_ids=body.empleado_ids,
        )
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    filename = f"hagemann_horas_{body.year}-{body.month:02d}.xlsx"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
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
    """
    Preview JSON de los datos que contendría el Excel del mes indicado.
    Útil para validar antes de descargar.
    """
    data = preview_reporte_mensual(db, year=year, month=month)
    return data
