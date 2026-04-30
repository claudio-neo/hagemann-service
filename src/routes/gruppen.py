"""
CRUD Abteilungen (Gruppen) + Kostenstellen — HG-Plan D
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from uuid import UUID

from ..database import get_db
from ..models.empleado import Grupo, CentroCoste

router = APIRouter(tags=["Stammdaten"])


# ── Schemas ──────────────────────────────────────────────
class GruppeCreate(BaseModel):
    nombre: str
    descripcion: Optional[str] = None

class GruppeUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    activo: Optional[bool] = None

class KostenstelleCreate(BaseModel):
    codigo: str
    nombre: str
    descripcion: Optional[str] = None
    color: Optional[str] = None

class KostenstelleUpdate(BaseModel):
    codigo: Optional[str] = None
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    color: Optional[str] = None
    activo: Optional[bool] = None


# ── Abteilungen (Gruppen) ───────────────────────────────
@router.get("/abteilungen/")
def list_abteilungen(activo: Optional[bool] = None, db: Session = Depends(get_db)):
    q = db.query(Grupo)
    if activo is not None:
        q = q.filter(Grupo.activo == activo)
    items = q.order_by(Grupo.orden, Grupo.nombre).all()
    return {"data": [_grupo_dict(g) for g in items]}


@router.post("/abteilungen/", status_code=201)
def create_abteilung(data: GruppeCreate, db: Session = Depends(get_db)):
    existing = db.query(Grupo).filter(Grupo.nombre == data.nombre).first()
    if existing:
        raise HTTPException(409, f"Abteilung '{data.nombre}' existiert bereits")
    g = Grupo(nombre=data.nombre, descripcion=data.descripcion)
    db.add(g)
    db.commit()
    db.refresh(g)
    return _grupo_dict(g)


@router.put("/abteilungen/{abt_id}")
def update_abteilung(abt_id: UUID, data: GruppeUpdate, db: Session = Depends(get_db)):
    g = db.query(Grupo).filter(Grupo.id == abt_id).first()
    if not g:
        raise HTTPException(404, "Abteilung nicht gefunden")
    if data.nombre is not None:
        g.nombre = data.nombre
    if data.descripcion is not None:
        g.descripcion = data.descripcion
    if data.activo is not None:
        g.activo = data.activo
    db.commit()
    db.refresh(g)
    return _grupo_dict(g)


@router.delete("/abteilungen/{abt_id}", status_code=204)
def delete_abteilung(abt_id: UUID, db: Session = Depends(get_db)):
    g = db.query(Grupo).filter(Grupo.id == abt_id).first()
    if not g:
        raise HTTPException(404, "Abteilung nicht gefunden")
    g.activo = False
    db.commit()


# ── Kostenstellen ────────────────────────────────────────
@router.get("/kostenstellen/")
def list_kostenstellen(activo: Optional[bool] = None, db: Session = Depends(get_db)):
    q = db.query(CentroCoste)
    if activo is not None:
        q = q.filter(CentroCoste.activo == activo)
    items = q.order_by(CentroCoste.codigo).all()
    return {"data": [_cc_dict(c) for c in items]}


@router.post("/kostenstellen/", status_code=201)
def create_kostenstelle(data: KostenstelleCreate, db: Session = Depends(get_db)):
    existing = db.query(CentroCoste).filter(CentroCoste.codigo == data.codigo).first()
    if existing:
        raise HTTPException(409, f"Kostenstelle '{data.codigo}' existiert bereits")
    c = CentroCoste(
        codigo=data.codigo, nombre=data.nombre,
        descripcion=data.descripcion, color=data.color
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _cc_dict(c)


@router.put("/kostenstellen/{ks_id}")
def update_kostenstelle(ks_id: UUID, data: KostenstelleUpdate, db: Session = Depends(get_db)):
    c = db.query(CentroCoste).filter(CentroCoste.id == ks_id).first()
    if not c:
        raise HTTPException(404, "Kostenstelle nicht gefunden")
    for field in ("codigo", "nombre", "descripcion", "color", "activo"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(c, field, val)
    db.commit()
    db.refresh(c)
    return _cc_dict(c)


@router.delete("/kostenstellen/{ks_id}", status_code=204)
def delete_kostenstelle(ks_id: UUID, db: Session = Depends(get_db)):
    c = db.query(CentroCoste).filter(CentroCoste.id == ks_id).first()
    if not c:
        raise HTTPException(404, "Kostenstelle nicht gefunden")
    c.activo = False
    db.commit()


# ── Helpers ──────────────────────────────────────────────
def _grupo_dict(g: Grupo) -> dict:
    return {
        "id": str(g.id), "nombre": g.nombre,
        "descripcion": g.descripcion, "activo": g.activo,
        "orden": g.orden,
    }

def _cc_dict(c: CentroCoste) -> dict:
    return {
        "id": str(c.id), "codigo": c.codigo, "nombre": c.nombre,
        "descripcion": c.descripcion, "color": c.color, "activo": c.activo,
    }
