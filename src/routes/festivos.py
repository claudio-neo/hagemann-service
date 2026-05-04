"""
API de Festivos (Feiertage)
CRUD para días festivos nacionales y regionales (Sachsen).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import date

from ..database import get_db
from ..models.vacaciones import Festivo, TipoFestivo
from ..auth import require_permission
from ..permisos import TIMECLOCK_REGISTER, USERS_ADMIN
from ..services.audit_service import log_action, diff_changes

router = APIRouter(
    prefix="/festivos",
    tags=["Festivos"],
    dependencies=[Depends(require_permission(TIMECLOCK_REGISTER))],
)


# ========== SCHEMAS ==========

class FestivoCreate(BaseModel):
    fecha: date
    nombre: str
    bundesland: str = "DE"
    tipo: TipoFestivo = TipoFestivo.NACIONAL
    activo: bool = True


class FestivoUpdate(BaseModel):
    nombre: Optional[str] = None
    bundesland: Optional[str] = None
    tipo: Optional[TipoFestivo] = None
    activo: Optional[bool] = None


def _to_dict(f: Festivo) -> dict:
    return {
        "id": str(f.id),
        "fecha": f.fecha.isoformat(),
        "nombre": f.nombre,
        "bundesland": f.bundesland,
        "tipo": f.tipo,
        "activo": f.activo,
    }


# ========== ENDPOINTS ==========

@router.get("/")
def listar_festivos(
    anio: Optional[int] = Query(None, description="Filtrar por año (ej: 2026)"),
    bundesland: Optional[str] = Query(None, description="Filtrar por Bundesland (ej: SN, DE)"),
    activo: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    """Lista festivos con filtros opcionales por año y Bundesland."""
    query = db.query(Festivo)

    if anio:
        from sqlalchemy import extract
        query = query.filter(extract("year", Festivo.fecha) == anio)
    if bundesland:
        query = query.filter(Festivo.bundesland == bundesland.upper())
    if activo is not None:
        query = query.filter(Festivo.activo == activo)

    festivos = query.order_by(Festivo.fecha).all()
    return {
        "data": [_to_dict(f) for f in festivos],
        "total": len(festivos),
    }


@router.get("/{festivo_id}")
def obtener_festivo(festivo_id: UUID, db: Session = Depends(get_db)):
    """Obtiene un festivo por ID."""
    f = db.query(Festivo).filter(Festivo.id == festivo_id).first()
    if not f:
        raise HTTPException(404, "Feiertag nicht gefunden")
    return _to_dict(f)


@router.post("/", status_code=201)
def crear_festivo(data: FestivoCreate, db: Session = Depends(get_db), _auth=Depends(require_permission(USERS_ADMIN))):
    """Crea un nuevo festivo. Falla si existiert bereits el mismo fecha+bundesland."""
    existing = db.query(Festivo).filter(
        Festivo.fecha == data.fecha,
        Festivo.bundesland == data.bundesland.upper(),
    ).first()
    if existing:
        raise HTTPException(
            409,
            f"Ya existe un festivo en {data.fecha} para {data.bundesland}"
        )

    festivo = Festivo(
        fecha=data.fecha,
        nombre=data.nombre,
        bundesland=data.bundesland.upper(),
        tipo=data.tipo,
        activo=data.activo,
    )
    db.add(festivo)
    db.flush()
    log_action(db, "CREATE", "festivo",
               entidad_id=str(festivo.id),
               entidad_label=f"{festivo.fecha.isoformat()} – {festivo.nombre}",
               descripcion=f"Feiertag '{festivo.nombre}' ({festivo.bundesland}) erstellt",
               usuario_nick=_auth.nick)
    db.commit()
    db.refresh(festivo)
    return {"id": str(festivo.id), "message": "Festivo creado", **_to_dict(festivo)}


@router.post("/bulk", status_code=201)
def crear_festivos_bulk(
    festivos: List[FestivoCreate], db: Session = Depends(get_db),
    _auth=Depends(require_permission(USERS_ADMIN)),
):
    """
    Crea múltiples festivos de una vez.
    Omite los que existiert bereitsn (upsert por fecha+bundesland).
    """
    created = 0
    skipped = 0
    for data in festivos:
        existing = db.query(Festivo).filter(
            Festivo.fecha == data.fecha,
            Festivo.bundesland == data.bundesland.upper(),
        ).first()
        if existing:
            skipped += 1
            continue
        festivo = Festivo(
            fecha=data.fecha,
            nombre=data.nombre,
            bundesland=data.bundesland.upper(),
            tipo=data.tipo,
            activo=data.activo,
        )
        db.add(festivo)
        created += 1

    log_action(db, "IMPORT", "festivo",
               descripcion=f"Bulk-Import: {created} Feiertage erstellt, {skipped} übersprungen",
               usuario_nick=_auth.nick)
    db.commit()
    return {
        "message": f"{created} festivos creados, {skipped} omitidos (ya existían)",
        "created": created,
        "skipped": skipped,
    }


@router.put("/{festivo_id}")
def actualizar_festivo(
    festivo_id: UUID, data: FestivoUpdate, db: Session = Depends(get_db),
    _auth=Depends(require_permission(USERS_ADMIN)),
):
    """Actualiza un festivo."""
    f = db.query(Festivo).filter(Festivo.id == festivo_id).first()
    if not f:
        raise HTTPException(404, "Feiertag nicht gefunden")
    update_fields = data.model_dump(exclude_unset=True)
    old_state = {k: str(getattr(f, k)) for k in update_fields}
    for field, value in update_fields.items():
        setattr(f, field, value)
    new_state = {k: str(getattr(f, k)) for k in update_fields}
    log_action(db, "UPDATE", "festivo",
               entidad_id=str(f.id),
               entidad_label=f"{f.fecha.isoformat()} – {f.nombre}",
               cambios=diff_changes(old_state, new_state),
               usuario_nick=_auth.nick)
    db.commit()
    db.refresh(f)
    return {"message": "Festivo actualizado", **_to_dict(f)}


@router.delete("/{festivo_id}")
def eliminar_festivo(festivo_id: UUID, db: Session = Depends(get_db), _auth=Depends(require_permission(USERS_ADMIN))):
    """Elimina un festivo (baja física)."""
    f = db.query(Festivo).filter(Festivo.id == festivo_id).first()
    if not f:
        raise HTTPException(404, "Feiertag nicht gefunden")
    log_action(db, "DELETE", "festivo",
               entidad_id=str(f.id),
               entidad_label=f"{f.fecha.isoformat()} – {f.nombre}",
               descripcion=f"Feiertag '{f.nombre}' ({f.bundesland}) gelöscht",
               usuario_nick=_auth.nick)
    db.delete(f)
    db.commit()
    return {"message": "Festivo eliminado"}


@router.get("/check/{fecha_str}")
def es_festivo(
    fecha_str: str,
    bundesland: str = Query("SN", description="Bundesland a comprobar"),
    db: Session = Depends(get_db),
):
    """
    Comprueba si una fecha es festivo.
    Devuelve festivos nacionales (DE) + los del Bundesland indicado.
    """
    try:
        fecha = date.fromisoformat(fecha_str)
    except ValueError:
        raise HTTPException(400, "Formato de fecha inválido. Usa YYYY-MM-DD")

    festivos = db.query(Festivo).filter(
        Festivo.fecha == fecha,
        Festivo.activo == True,
        Festivo.bundesland.in_(["DE", bundesland.upper()]),
    ).all()

    return {
        "fecha": fecha_str,
        "bundesland": bundesland.upper(),
        "es_festivo": len(festivos) > 0,
        "festivos": [_to_dict(f) for f in festivos],
    }
