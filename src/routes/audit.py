"""
API Audit Log — historial de cambios (HG-Plan G)
+ InteractionLog — registro profundo de interacciones (depuración forense)
"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel

from ..database import get_db
from ..models.audit import AuditLog, InteractionLog
from ..auth import require_permission, get_current_user
from ..permisos import USERS_ADMIN
from ..models.usuario import Usuario

router = APIRouter(
    prefix="/audit",
    tags=["Audit"],
)


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT LOG (admin-only)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/", dependencies=[Depends(require_permission(USERS_ADMIN))])
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


# ═══════════════════════════════════════════════════════════════════════════════
# INTERACTION LOG (deep forensic log)
# ═══════════════════════════════════════════════════════════════════════════════

class InteractionEntry(BaseModel):
    action: str           # click | navigate | api_call | select | login | logout | error
    target: Optional[str] = None
    detail: Optional[str] = None
    page: Optional[str] = None
    timestamp: Optional[str] = None  # ISO string from client


class InteractionBatch(BaseModel):
    source: str = "admin"            # admin | terminal
    user_nick: Optional[str] = None
    employee_name: Optional[str] = None
    employee_id: Optional[str] = None
    events: List[InteractionEntry]


@router.post("/interactions")
def log_interactions(
    batch: InteractionBatch,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Batch write de interacciones. Ligero en auth — acepta cualquier token válido.
    Si no hay token, guarda igualmente (terminal sin login puede generar eventos).
    """
    now = datetime.utcnow()

    # Intentar extraer usuario del token (no obligatorio)
    user_nick = batch.user_nick

    for ev in batch.events:
        ts = now
        if ev.timestamp:
            try:
                ts = datetime.fromisoformat(ev.timestamp.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, TypeError):
                ts = now

        entry = InteractionLog(
            user_nick=user_nick,
            employee_name=batch.employee_name,
            employee_id=batch.employee_id,
            action=ev.action,
            target=ev.target,
            detail=ev.detail[:2000] if ev.detail and len(ev.detail) > 2000 else ev.detail,
            page=ev.page,
            source=batch.source,
            timestamp=ts,
            server_ts=now,
        )
        db.add(entry)

    db.commit()
    return {"ok": True, "count": len(batch.events)}


@router.get("/interactions", dependencies=[Depends(require_permission(USERS_ADMIN))])
def list_interactions(
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    user_nick: Optional[str] = None,
    employee_name: Optional[str] = None,
    action: Optional[str] = None,
    source: Optional[str] = None,
    page: Optional[str] = None,
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Lista interacciones con filtros — solo Admin."""
    q = db.query(InteractionLog)
    if desde:
        q = q.filter(InteractionLog.timestamp >= datetime.combine(desde, datetime.min.time()))
    if hasta:
        q = q.filter(InteractionLog.timestamp <= datetime.combine(hasta, datetime.max.time()))
    if user_nick:
        q = q.filter(InteractionLog.user_nick == user_nick)
    if employee_name:
        q = q.filter(InteractionLog.employee_name.ilike(f"%{employee_name}%"))
    if action:
        q = q.filter(InteractionLog.action == action)
    if source:
        q = q.filter(InteractionLog.source == source)
    if page:
        q = q.filter(InteractionLog.page == page)

    total = q.count()
    items = q.order_by(InteractionLog.timestamp.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "data": [
            {
                "id": str(e.id),
                "user_nick": e.user_nick,
                "employee_name": e.employee_name,
                "employee_id": e.employee_id,
                "action": e.action,
                "target": e.target,
                "detail": e.detail,
                "page": e.page,
                "source": e.source,
                "timestamp": e.timestamp.isoformat() + "Z" if e.timestamp else None,
                "server_ts": e.server_ts.isoformat() + "Z" if e.server_ts else None,
            }
            for e in items
        ],
    }
