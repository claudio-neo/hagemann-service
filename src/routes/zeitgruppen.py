"""
CRUD Zeitgruppen — reglas de cálculo horario (HG-Plan A.4)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import time

from ..database import get_db
from ..models.empleado import Zeitgruppe
from ..auth import require_permission
from ..permisos import TIMECLOCK_REGISTER, EMPLOYEES_EDIT

router = APIRouter(
    prefix="/zeitgruppen",
    tags=["Stammdaten"],
    dependencies=[Depends(require_permission(TIMECLOCK_REGISTER))],
)


class ZeitgruppeCreate(BaseModel):
    nombre: str
    descripcion: Optional[str] = None
    tipo: str = "GLEITZEIT"  # GLEITZEIT | VERWALTUNG | SCHICHT
    hora_minima_inicio: Optional[str] = None  # "07:00"
    usar_inicio_turno: bool = False
    rotacion_semanal: bool = False

class ZeitgruppeUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    tipo: Optional[str] = None
    hora_minima_inicio: Optional[str] = None
    usar_inicio_turno: Optional[bool] = None
    rotacion_semanal: Optional[bool] = None
    activo: Optional[bool] = None


@router.get("/")
def list_zeitgruppen(activo: Optional[bool] = None, db: Session = Depends(get_db)):
    q = db.query(Zeitgruppe)
    if activo is not None:
        q = q.filter(Zeitgruppe.activo == activo)
    items = q.order_by(Zeitgruppe.nombre).all()
    return {"data": [_zg_dict(z) for z in items]}


@router.post("/", status_code=201)
def create_zeitgruppe(data: ZeitgruppeCreate, db: Session = Depends(get_db), _auth=Depends(require_permission(EMPLOYEES_EDIT))):
    existing = db.query(Zeitgruppe).filter(Zeitgruppe.nombre == data.nombre).first()
    if existing:
        raise HTTPException(409, f"Zeitgruppe '{data.nombre}' existiert bereits")
    z = Zeitgruppe(
        nombre=data.nombre,
        descripcion=data.descripcion,
        tipo=data.tipo,
        hora_minima_inicio=_parse_time(data.hora_minima_inicio),
        usar_inicio_turno=data.usar_inicio_turno,
        rotacion_semanal=data.rotacion_semanal,
    )
    db.add(z)
    db.commit()
    db.refresh(z)
    return _zg_dict(z)


@router.put("/{zg_id}")
def update_zeitgruppe(zg_id: UUID, data: ZeitgruppeUpdate, db: Session = Depends(get_db), _auth=Depends(require_permission(EMPLOYEES_EDIT))):
    z = db.query(Zeitgruppe).filter(Zeitgruppe.id == zg_id).first()
    if not z:
        raise HTTPException(404, "Zeitgruppe nicht gefunden")
    if data.nombre is not None:
        z.nombre = data.nombre
    if data.descripcion is not None:
        z.descripcion = data.descripcion
    if data.tipo is not None:
        z.tipo = data.tipo
    if data.hora_minima_inicio is not None:
        z.hora_minima_inicio = _parse_time(data.hora_minima_inicio)
    if data.usar_inicio_turno is not None:
        z.usar_inicio_turno = data.usar_inicio_turno
    if data.rotacion_semanal is not None:
        z.rotacion_semanal = data.rotacion_semanal
    if data.activo is not None:
        z.activo = data.activo
    db.commit()
    db.refresh(z)
    return _zg_dict(z)


@router.delete("/{zg_id}", status_code=204)
def delete_zeitgruppe(zg_id: UUID, db: Session = Depends(get_db), _auth=Depends(require_permission(EMPLOYEES_EDIT))):
    z = db.query(Zeitgruppe).filter(Zeitgruppe.id == zg_id).first()
    if not z:
        raise HTTPException(404, "Zeitgruppe nicht gefunden")
    z.activo = False
    db.commit()


def _parse_time(val) -> Optional[time]:
    if not val:
        return None
    parts = str(val).split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def _zg_dict(z: Zeitgruppe) -> dict:
    return {
        "id": str(z.id),
        "nombre": z.nombre,
        "descripcion": z.descripcion,
        "tipo": z.tipo,
        "hora_minima_inicio": z.hora_minima_inicio.strftime("%H:%M") if z.hora_minima_inicio else None,
        "usar_inicio_turno": z.usar_inicio_turno,
        "rotacion_semanal": z.rotacion_semanal,
        "activo": z.activo,
    }
