"""
CRUD Zeitgruppen — grupos horarios (HG-Plan A)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import time

from ..database import get_db
from ..models.empleado import Zeitgruppe

router = APIRouter(prefix="/zeitgruppen", tags=["Stammdaten"])


class ZeitgruppeCreate(BaseModel):
    nombre: str
    descripcion: Optional[str] = None
    tipo: str = "GLEITZEIT"  # GLEITZEIT | VERWALTUNG | SCHICHT
    hora_minima_inicio: Optional[str] = None  # "HH:MM"
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


@router.get("/")
def list_zeitgruppen(db: Session = Depends(get_db)):
    items = db.query(Zeitgruppe).filter(Zeitgruppe.activo == True).order_by(Zeitgruppe.nombre).all()
    return {"data": [_zg_dict(z) for z in items]}


@router.post("/", status_code=201)
def create_zeitgruppe(data: ZeitgruppeCreate, db: Session = Depends(get_db)):
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
def update_zeitgruppe(zg_id: UUID, data: ZeitgruppeUpdate, db: Session = Depends(get_db)):
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


def _parse_time(val: Optional[str]) -> Optional[time]:
    if not val:
        return None
    parts = val.split(":")
    return time(int(parts[0]), int(parts[1]))
