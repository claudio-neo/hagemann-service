"""
API Audit Log — historial de cambios (HG-Plan G)
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date, datetime

from ..database import get_db
from ..models.audit import AuditLog
from ..auth import require_permission
from ..permisos import USERS_ADMIN

router = APIRouter(
    prefix="/audit",
    tags=["Audit"],
    dependencies=[Depends(require_permission(USERS_ADMIN))],
)


@router.get("/")
def list_audit_log(
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    entidad_tipo: Optional[str] = None,
    usuario_nick: Optional[str] = None,
    accion: Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Lista entradas del audit log con filtros opcionales."""
    q = db.query(AuditLog)
    if desde:
        q = q.filter(AuditLog.created_at >= datetime.combine(desde, datetime.min.time()))
    if hasta:
        q = q.filter(AuditLog.created_at <= datetime.combine(hasta, datetime.max.time()))
    if entidad_tipo:
        q = q.filter(AuditLog.entidad_tipo == entidad_tipo)
    if usuario_nick:
        q = q.filter(AuditLog.usuario_nick == usuario_nick)
    if accion:
        q = q.filter(AuditLog.accion == accion)

    total = q.count()
    items = q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "data": [
            {
                "id": str(e.id),
                "accion": e.accion,
                "entidad_tipo": e.entidad_tipo,
                "entidad_id": e.entidad_id,
                "entidad_label": e.entidad_label,
                "cambios": e.cambios,
                "descripcion": e.descripcion,
                "usuario_id": str(e.usuario_id) if e.usuario_id else None,
                "usuario_nick": e.usuario_nick,
                "ip_address": e.ip_address,
                "created_at": e.created_at.isoformat() + "Z" if e.created_at else None,
            }
            for e in items
        ],
    }
