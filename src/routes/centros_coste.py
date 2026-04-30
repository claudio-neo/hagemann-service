"""
API de Centros de Coste / Departamentos
CRUD para gestión de centros donde se imputan horas
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID

from ..database import get_db
from ..models.empleado import CentroCoste

router = APIRouter(prefix="/centros-coste", tags=["Centros de Coste"])


class CentroCosteCreate(BaseModel):
    codigo: str
    nombre: str
    descripcion: Optional[str] = None
    color: Optional[str] = None


class CentroCosteUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    color: Optional[str] = None
    activo: Optional[bool] = None


@router.get("/")
def listar_centros(
    activo: Optional[bool] = True,
    db: Session = Depends(get_db),
):
    query = db.query(CentroCoste)
    if activo is not None:
        query = query.filter(CentroCoste.activo == activo)
    centros = query.order_by(CentroCoste.codigo).all()
    return {
        "data": [
            {
                "id": str(c.id),
                "codigo": c.codigo,
                "nombre": c.nombre,
                "descripcion": c.descripcion,
                "color": c.color,
                "activo": c.activo,
            }
            for c in centros
        ],
        "total": len(centros),
    }


@router.post("/", status_code=201)
def crear_centro(data: CentroCosteCreate, db: Session = Depends(get_db)):
    existing = db.query(CentroCoste).filter(
        CentroCoste.codigo == data.codigo
    ).first()
    if existing:
        raise HTTPException(409, f"Código {data.codigo} existiert bereits")
    cc = CentroCoste(**data.model_dump())
    db.add(cc)
    db.commit()
    db.refresh(cc)
    return {"id": str(cc.id), "codigo": cc.codigo, "message": "Centro de coste creado"}


@router.put("/{centro_id}")
def actualizar_centro(
    centro_id: UUID, data: CentroCosteUpdate, db: Session = Depends(get_db)
):
    cc = db.query(CentroCoste).filter(CentroCoste.id == centro_id).first()
    if not cc:
        raise HTTPException(404, "Kostenstelle nicht gefunden")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(cc, field, value)
    db.commit()
    return {"message": "Centro de coste actualizado"}
