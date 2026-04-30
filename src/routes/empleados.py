"""
API de Empleados Hagemann
CRUD completo — HG-21
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import date, datetime
import logging

from ..database import get_db
from ..models.empleado import Empleado, Grupo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/empleados", tags=["Empleados"])


# ========== SCHEMAS ==========

class EmpleadoCreate(BaseModel):
    """
    Crear empleado. id_nummer se auto-genera si no se provee.
    """
    id_nummer: Optional[int] = None        # auto si None
    nombre: str
    apellido: Optional[str] = None
    email: Optional[str] = None
    id_nfc: Optional[str] = None           # alias para nfc_tag
    nfc_tag: Optional[str] = None
    keytag: Optional[str] = None
    grupo_id: Optional[UUID] = None
    monthly_hours: int = 160
    salary_hour: Optional[float] = None
    telefono: Optional[str] = None
    fecha_alta: Optional[date] = None
    activo: bool = True


class EmpleadoUpdate(BaseModel):
    nombre: Optional[str] = None
    apellido: Optional[str] = None
    email: Optional[str] = None
    nfc_tag: Optional[str] = None
    keytag: Optional[str] = None
    grupo_id: Optional[UUID] = None
    monthly_hours: Optional[int] = None
    salary_hour: Optional[float] = None
    telefono: Optional[str] = None
    fecha_alta: Optional[date] = None
    fecha_baja: Optional[date] = None
    activo: Optional[bool] = None


class NfcUpdateBody(BaseModel):
    id_nfc: str
    motivo: Optional[str] = "Sin motivo especificado"


class EmpleadoOut(BaseModel):
    id: UUID
    id_nummer: int
    nombre: str
    apellido: Optional[str]
    email: Optional[str]
    nfc_tag: Optional[str]
    keytag: Optional[str]
    grupo_id: Optional[UUID]
    grupo_nombre: Optional[str] = None
    monthly_hours: int
    salary_hour: Optional[float]
    telefono: Optional[str]
    activo: bool
    fecha_alta: Optional[date]
    fecha_baja: Optional[date]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


def _to_out(e: Empleado) -> dict:
    return {
        "id": e.id,
        "id_nummer": e.id_nummer,
        "personalnummer": e.personalnummer,
        "benutzer_id": e.benutzer_id,
        "nombre": e.nombre,
        "apellido": e.apellido,
        "email": e.email,
        "nfc_tag": e.nfc_tag,
        "keytag": e.keytag,
        "grupo_id": e.grupo_id,
        "grupo_nombre": e.grupo.nombre if e.grupo else None,
        "kostenstelle_id": e.kostenstelle_id,
        "kostenstelle_nombre": e.kostenstelle.nombre if e.kostenstelle else None,
        "zeitgruppe_id": e.zeitgruppe_id,
        "zeitgruppe_nombre": e.zeitgruppe.nombre if e.zeitgruppe else None,
        "monthly_hours": e.monthly_hours,
        "salary_hour": float(e.salary_hour) if e.salary_hour is not None else None,
        "telefono": e.telefono,
        "beginn_berechnung": e.beginn_berechnung.isoformat() if e.beginn_berechnung else None,
        "mandat": e.mandat,
        "firmenbereich": e.firmenbereich,
        "activo": e.activo,
        "fecha_alta": e.fecha_alta,
        "fecha_baja": e.fecha_baja,
        "created_at": e.created_at,
    }


def _next_id_nummer(db: Session) -> int:
    """Auto-generate sequential id_nummer = MAX + 1."""
    max_val = db.query(func.max(Empleado.id_nummer)).scalar()
    return (max_val or 0) + 1


# ========== ENDPOINTS ==========

@router.get("/")
def listar_empleados(
    activo: Optional[bool] = None,
    grupo_id: Optional[UUID] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(Empleado).options(joinedload(Empleado.grupo))
    if activo is not None:
        query = query.filter(Empleado.activo == activo)
    if grupo_id:
        query = query.filter(Empleado.grupo_id == grupo_id)
    if q:
        query = query.filter(
            Empleado.nombre.ilike(f"%{q}%")
            | Empleado.apellido.ilike(f"%{q}%")
        )
    empleados = query.order_by(Empleado.id_nummer).all()
    return {"data": [_to_out(e) for e in empleados], "total": len(empleados)}


@router.get("/{empleado_id}")
def obtener_empleado(empleado_id: UUID, db: Session = Depends(get_db)):
    e = (db.query(Empleado)
         .options(joinedload(Empleado.grupo))
         .filter(Empleado.id == empleado_id)
         .first())
    if not e:
        raise HTTPException(404, "Empleado no encontrado")
    return _to_out(e)


@router.post("/", status_code=201)
def crear_empleado(data: EmpleadoCreate, db: Session = Depends(get_db)):
    """
    Crear empleado. Si no se provee id_nummer, se auto-genera secuencial.
    id_nfc es alias de nfc_tag.
    """
    # Resolver id_nummer
    if data.id_nummer is not None:
        existing = db.query(Empleado).filter(
            Empleado.id_nummer == data.id_nummer
        ).first()
        if existing:
            raise HTTPException(409, f"id_nummer {data.id_nummer} ya existe")
        id_nummer = data.id_nummer
    else:
        id_nummer = _next_id_nummer(db)

    # Resolver nfc_tag (id_nfc es alias)
    nfc_tag = data.id_nfc or data.nfc_tag

    emp = Empleado(
        id_nummer=id_nummer,
        nombre=data.nombre,
        apellido=data.apellido,
        email=data.email,
        nfc_tag=nfc_tag,
        keytag=data.keytag,
        grupo_id=data.grupo_id,
        monthly_hours=data.monthly_hours,
        salary_hour=data.salary_hour,
        telefono=data.telefono,
        fecha_alta=data.fecha_alta,
        activo=data.activo,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return {
        "id": str(emp.id),
        "id_nummer": emp.id_nummer,
        "message": "Empleado creado",
    }


@router.put("/{empleado_id}")
def actualizar_empleado(
    empleado_id: UUID, data: EmpleadoUpdate, db: Session = Depends(get_db)
):
    """Actualiza todos los campos relevantes del empleado."""
    emp = (db.query(Empleado)
           .options(joinedload(Empleado.grupo))
           .filter(Empleado.id == empleado_id)
           .first())
    if not emp:
        raise HTTPException(404, "Empleado no encontrado")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(emp, field, value)

    emp.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(emp)
    return {"message": "Empleado actualizado", "empleado": _to_out(emp)}


@router.put("/{empleado_id}/nfc")
def cambiar_nfc(
    empleado_id: UUID,
    body: NfcUpdateBody,
    db: Session = Depends(get_db),
):
    """
    Cambia el NFC tag del empleado.
    Registra el cambio en el log con motivo.
    """
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Empleado no encontrado")

    old_nfc = emp.nfc_tag
    emp.nfc_tag = body.id_nfc
    emp.updated_at = datetime.utcnow()
    db.commit()

    # Log del cambio
    logger.info(
        "NFC_CHANGE | empleado=%s | id_nummer=%s | old=%s | new=%s | motivo=%s",
        str(empleado_id), emp.id_nummer, old_nfc, body.id_nfc, body.motivo,
    )

    return {
        "message": "NFC tag actualizado",
        "empleado_id": str(empleado_id),
        "id_nummer": emp.id_nummer,
        "nfc_tag_anterior": old_nfc,
        "nfc_tag_nuevo": body.id_nfc,
        "motivo": body.motivo,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.delete("/{empleado_id}")
def desactivar_empleado(
    empleado_id: UUID,
    db: Session = Depends(get_db),
):
    """
    Soft delete: marca al empleado como inactivo (activo=False).
    NO elimina el registro de la base de datos.
    """
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Empleado no encontrado")
    if not emp.activo:
        return {
            "message": "El empleado ya estaba inactivo",
            "empleado_id": str(empleado_id),
            "id_nummer": emp.id_nummer,
        }

    emp.activo = False
    emp.fecha_baja = date.today()
    emp.updated_at = datetime.utcnow()
    db.commit()

    return {
        "message": f"Empleado {emp.id_nummer} desactivado (soft delete)",
        "empleado_id": str(empleado_id),
        "id_nummer": emp.id_nummer,
        "fecha_baja": emp.fecha_baja.isoformat(),
    }


@router.post("/{empleado_id}/reactivar")
def reactivar_empleado(
    empleado_id: UUID,
    db: Session = Depends(get_db),
):
    """
    Reactiva un empleado previamente desactivado (activo=True).
    Limpia la fecha_baja.
    """
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Empleado no encontrado")
    if emp.activo:
        return {
            "message": "El empleado ya estaba activo",
            "empleado_id": str(empleado_id),
            "id_nummer": emp.id_nummer,
        }

    emp.activo = True
    emp.fecha_baja = None
    emp.updated_at = datetime.utcnow()
    db.commit()

    return {
        "message": f"Empleado {emp.id_nummer} reactivado",
        "empleado_id": str(empleado_id),
        "id_nummer": emp.id_nummer,
    }
