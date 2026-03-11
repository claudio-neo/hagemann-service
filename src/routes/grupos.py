"""
API de Grupos — CRUD básico
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from uuid import UUID

from ..database import get_db
from ..models.empleado import Grupo

router = APIRouter(prefix="/grupos", tags=["Grupos"])


class GrupoCreate(BaseModel):
    nombre: str
    descripcion: Optional[str] = None


class GrupoUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    activo: Optional[bool] = None


@router.get("/")
def listar_grupos(activo: Optional[bool] = True, db: Session = Depends(get_db)):
    query = db.query(Grupo)
    if activo is not None:
        query = query.filter(Grupo.activo == activo)
    grupos = query.order_by(Grupo.orden, Grupo.nombre).all()
    return {
        "data": [
            {"id": str(g.id), "nombre": g.nombre, "descripcion": g.descripcion, "activo": g.activo}
            for g in grupos
        ],
        "total": len(grupos),
    }


@router.post("/", status_code=201)
def crear_grupo(data: GrupoCreate, db: Session = Depends(get_db)):
    g = Grupo(**data.model_dump())
    db.add(g)
    db.commit()
    db.refresh(g)
    return {"id": str(g.id), "nombre": g.nombre, "message": "Grupo creado"}


@router.put("/{grupo_id}")
def actualizar_grupo(grupo_id: UUID, data: GrupoUpdate, db: Session = Depends(get_db)):
    g = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not g:
        raise HTTPException(404, "Grupo no encontrado")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(g, field, value)
    db.commit()
    return {"message": "Grupo actualizado"}
