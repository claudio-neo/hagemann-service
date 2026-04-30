"""
API de Aprobaciones 2 niveles (HG-17)
Sistema genérico para aprobar cualquier entidad del sistema.
  - Nivel 1: Abteilungsleiter (jefe de departamento)
  - Nivel 2: Admin (aprobación final)
HG-16: al aprobar "correccion_fichaje" se aplican los cambios al fichaje original.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.aprobacion import AprobacionLog
from ..auth import require_permission
from ..permisos import APPROVALS_LEVEL1, APPROVALS_LEVEL2

router = APIRouter(prefix="/aprobaciones", tags=["Aprobaciones"])


# ─── Helper: aplicar corrección aprobada al fichaje ──────────────────────────

def _aplicar_correccion_si_procede(db: Session, tipo_entidad: str, entidad_id: str):
    """
    Si el tipo_entidad es 'correccion_fichaje' y la aprobación llega a APROBADA,
    aplica los valores solicitados al fichaje original (HG-16).
    Importación condicional para evitar ciclos con correcciones.py.
    """
    if tipo_entidad != "correccion_fichaje":
        return

    try:
        from ..models.correccion import SolicitudCorreccion
        from ..models.fichaje import Fichaje
    except ImportError:
        return

    solicitud = db.query(SolicitudCorreccion).filter(
        SolicitudCorreccion.id == entidad_id
    ).first()
    if not solicitud:
        return

    fichaje = db.query(Fichaje).filter(Fichaje.id == solicitud.fichaje_id).first()
    if not fichaje:
        return

    # Aplicar valores solicitados
    if solicitud.solicitada_entrada is not None:
        fichaje.fecha_entrada = solicitud.solicitada_entrada
    if solicitud.solicitada_salida is not None:
        fichaje.fecha_salida = solicitud.solicitada_salida
    if solicitud.solicitado_descanso_min is not None:
        fichaje.minutos_descanso = solicitud.solicitado_descanso_min

    fichaje.correccion = 1  # indica que fue corregido
    fichaje.updated_at = datetime.utcnow()

    # Actualizar también el estado de la solicitud
    solicitud.estado = "APROBADA"
    solicitud.fecha_revision = datetime.utcnow()
    solicitud.updated_at = datetime.utcnow()


def _rechazar_correccion_si_procede(db: Session, tipo_entidad: str, entidad_id: str, comentario: Optional[str] = None):
    """Marca la SolicitudCorreccion como RECHAZADA cuando el nivel2 rechaza."""
    if tipo_entidad != "correccion_fichaje":
        return

    try:
        from ..models.correccion import SolicitudCorreccion
    except ImportError:
        return

    solicitud = db.query(SolicitudCorreccion).filter(
        SolicitudCorreccion.id == entidad_id
    ).first()
    if not solicitud:
        return

    solicitud.estado = "RECHAZADA"
    solicitud.comentario_revision = comentario
    solicitud.fecha_revision = datetime.utcnow()
    solicitud.updated_at = datetime.utcnow()


# ─── Schemas ─────────────────────────────────────────────────────────────────

class AccionNivel1(BaseModel):
    """Abteilungsleiter propone o rechaza"""
    usuario: str
    accion: str  # PROPUESTA | RECHAZADA
    comentario: Optional[str] = None

class AccionNivel2(BaseModel):
    """Admin aprueba o rechaza definitivamente"""
    usuario: str
    accion: str  # APROBADA | RECHAZADA
    comentario: Optional[str] = None


# ─── Helpers ─────────────────────────────────────────────────────────────────

_ACCIONES_N1 = {"PROPUESTA", "RECHAZADA"}
_ACCIONES_N2 = {"APROBADA", "RECHAZADA"}


def _log_dict(log: AprobacionLog) -> dict:
    return {
        "id": str(log.id),
        "tipo_entidad": log.tipo_entidad,
        "entidad_id": log.entidad_id,
        "nivel1": {
            "usuario": log.nivel1_usuario,
            "accion": log.nivel1_accion,
            "fecha": log.nivel1_fecha.isoformat() + "Z" if log.nivel1_fecha else None,
            "comentario": log.nivel1_comentario,
        },
        "nivel2": {
            "usuario": log.nivel2_usuario,
            "accion": log.nivel2_accion,
            "fecha": log.nivel2_fecha.isoformat() + "Z" if log.nivel2_fecha else None,
            "comentario": log.nivel2_comentario,
        },
        "estado_final": log.estado_final,
        "created_at": log.created_at.isoformat() + "Z",
        "updated_at": log.updated_at.isoformat() + "Z",
    }


def _get_or_404(db: Session, tipo_entidad: str, entidad_id: str) -> AprobacionLog:
    log = db.query(AprobacionLog).filter(
        AprobacionLog.tipo_entidad == tipo_entidad,
        AprobacionLog.entidad_id == entidad_id,
    ).first()
    if not log:
        raise HTTPException(
            404,
            f"No se encontró registro de aprobación para {tipo_entidad}/{entidad_id}"
        )
    return log


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/pendientes")
def listar_pendientes(
    tipo_entidad: Optional[str] = None,
    estado: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _auth=Depends(require_permission(APPROVALS_LEVEL1)),
):
    """
    Lista aprobaciones pendientes (o filtradas por estado/tipo).
    Por defecto devuelve las que tienen estado_final = PENDIENTE o PROPUESTA.
    """
    q = db.query(AprobacionLog)
    if tipo_entidad:
        q = q.filter(AprobacionLog.tipo_entidad == tipo_entidad)
    if estado:
        q = q.filter(AprobacionLog.estado_final == estado.upper())
    else:
        # Default: solo las que aún no han sido resueltas
        q = q.filter(AprobacionLog.estado_final.in_(["PENDIENTE", "PROPUESTA"]))

    total = q.count()
    logs = (
        q.order_by(AprobacionLog.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "data": [_log_dict(l) for l in logs],
        "pagination": {"page": page, "limit": limit, "total": total},
    }


@router.get("/{tipo_entidad}/{entidad_id}")
def ver_aprobacion(
    tipo_entidad: str,
    entidad_id: str,
    db: Session = Depends(get_db),
    _auth=Depends(require_permission(APPROVALS_LEVEL1)),
):
    """Ver el estado de aprobación de una entidad concreta."""
    log = _get_or_404(db, tipo_entidad, entidad_id)
    return _log_dict(log)


@router.post("/{tipo_entidad}/{entidad_id}/nivel1", status_code=200)
def actuar_nivel1(
    tipo_entidad: str,
    entidad_id: str,
    data: AccionNivel1,
    db: Session = Depends(get_db),
    _auth=Depends(require_permission(APPROVALS_LEVEL1)),
):
    """
    Abteilungsleiter actúa (nivel 1).
    Acción: PROPUESTA (aprueba en su nivel) o RECHAZADA.
    Si rechaza, el estado_final pasa a RECHAZADA directamente.
    """
    if data.accion.upper() not in _ACCIONES_N1:
        raise HTTPException(400, f"Ungültige Aktion. Optionen: {_ACCIONES_N1}")

    log = db.query(AprobacionLog).filter(
        AprobacionLog.tipo_entidad == tipo_entidad,
        AprobacionLog.entidad_id == entidad_id,
    ).first()

    if not log:
        # Crear registro si no existe
        log = AprobacionLog(
            tipo_entidad=tipo_entidad,
            entidad_id=entidad_id,
            nivel1_accion="PENDIENTE",
            estado_final="PENDIENTE",
        )
        db.add(log)
        db.flush()

    # Verificar estado actual
    if log.estado_final in ("APROBADA", "RECHAZADA"):
        raise HTTPException(
            409,
            f"Esta solicitud ya está resuelta con estado: {log.estado_final}"
        )

    accion = data.accion.upper()
    log.nivel1_usuario = data.usuario
    log.nivel1_accion = accion
    log.nivel1_fecha = datetime.utcnow()
    log.nivel1_comentario = data.comentario

    if accion == "RECHAZADA":
        log.estado_final = "RECHAZADA"
        _rechazar_correccion_si_procede(db, tipo_entidad, entidad_id, data.comentario)
    else:  # PROPUESTA
        log.estado_final = "PROPUESTA"

    log.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(log)
    return _log_dict(log)


@router.post("/{tipo_entidad}/{entidad_id}/nivel2", status_code=200)
def actuar_nivel2(
    tipo_entidad: str,
    entidad_id: str,
    data: AccionNivel2,
    db: Session = Depends(get_db),
    _auth=Depends(require_permission(APPROVALS_LEVEL2)),
):
    """
    Admin actúa (nivel 2) — aprobación o rechazo final.
    Requiere que el nivel 1 ya haya propuesto (estado = PROPUESTA).
    Acción: APROBADA | RECHAZADA.
    """
    if data.accion.upper() not in _ACCIONES_N2:
        raise HTTPException(400, f"Ungültige Aktion. Optionen: {_ACCIONES_N2}")

    log = _get_or_404(db, tipo_entidad, entidad_id)

    if log.estado_final in ("APROBADA", "RECHAZADA"):
        raise HTTPException(
            409,
            f"Esta solicitud ya está resuelta con estado: {log.estado_final}"
        )
    if log.estado_final != "PROPUESTA":
        raise HTTPException(
            409,
            f"El nivel 1 aún no ha propuesto. Estado actual: {log.estado_final}"
        )

    accion = data.accion.upper()
    log.nivel2_usuario = data.usuario
    log.nivel2_accion = accion
    log.nivel2_fecha = datetime.utcnow()
    log.nivel2_comentario = data.comentario
    log.estado_final = accion  # APROBADA o RECHAZADA

    log.updated_at = datetime.utcnow()

    # HG-16: si la entidad es una corrección de fichaje, actualizar su estado
    if accion == "APROBADA":
        _aplicar_correccion_si_procede(db, tipo_entidad, entidad_id)
    elif accion == "RECHAZADA":
        _rechazar_correccion_si_procede(db, tipo_entidad, entidad_id, data.comentario)

    db.commit()
    db.refresh(log)
    return _log_dict(log)
