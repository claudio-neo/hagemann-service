"""
Servicio de auditoría — registra cambios administrativos (HG-Plan G)
"""
from sqlalchemy.orm import Session
from ..models.audit import AuditLog
from typing import Optional


def log_action(
    db: Session,
    accion: str,
    entidad_tipo: str,
    entidad_id: Optional[str] = None,
    entidad_label: Optional[str] = None,
    cambios: Optional[dict] = None,
    descripcion: Optional[str] = None,
    usuario_id: Optional[str] = None,
    usuario_nick: Optional[str] = None,
    ip_address: Optional[str] = None,
):
    """Registrar una acción en el audit log."""
    entry = AuditLog(
        accion=accion,
        entidad_tipo=entidad_tipo,
        entidad_id=str(entidad_id) if entidad_id else None,
        entidad_label=entidad_label,
        cambios=cambios,
        descripcion=descripcion,
        usuario_id=usuario_id,
        usuario_nick=usuario_nick,
        ip_address=ip_address,
    )
    db.add(entry)
    # Don't commit here — caller controls the transaction
    return entry


def diff_changes(old: dict, new: dict) -> dict:
    """Calcula diferencias entre estado anterior y nuevo."""
    changes = {}
    for key in set(list(old.keys()) + list(new.keys())):
        old_val = old.get(key)
        new_val = new.get(key)
        if str(old_val) != str(new_val):
            changes[key] = {"vorher": old_val, "nachher": new_val}
    return changes
