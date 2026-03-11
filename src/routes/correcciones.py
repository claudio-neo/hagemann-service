"""
API de Solicitudes de Corrección de Fichaje (HG-16)
Aprobación via el sistema genérico de 2 niveles (HG-17).
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.correccion import SolicitudCorreccion
from ..models.fichaje import Fichaje
from ..models.empleado import Empleado
from ..models.aprobacion import AprobacionLog

router = APIRouter(prefix="/correcciones", tags=["Correcciones de Fichaje"])

TIPO_ENTIDAD = "correccion_fichaje"


# ─── Schemas ─────────────────────────────────────────────────────────────────

class CorreccionCreate(BaseModel):
    fichaje_id: UUID
    empleado_id: UUID
    solicitada_entrada: Optional[datetime] = None
    solicitada_salida: Optional[datetime] = None
    solicitado_descanso_min: Optional[int] = None
    motivo: str
    solicitado_por: Optional[str] = None  # nick del usuario


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _correc_dict(c: SolicitudCorreccion, emp: Empleado = None) -> dict:
    e = emp or c.empleado
    return {
        "id": str(c.id),
        "fichaje_id": str(c.fichaje_id),
        "empleado_id": str(c.empleado_id),
        "empleado_nombre": f"{e.nombre} {e.apellido or ''}".strip() if e else None,
        "original_entrada": c.original_entrada.isoformat() + "Z" if c.original_entrada else None,
        "original_salida": c.original_salida.isoformat() + "Z" if c.original_salida else None,
        "original_descanso_min": c.original_descanso_min,
        "solicitada_entrada": c.solicitada_entrada.isoformat() + "Z" if c.solicitada_entrada else None,
        "solicitada_salida": c.solicitada_salida.isoformat() + "Z" if c.solicitada_salida else None,
        "solicitado_descanso_min": c.solicitado_descanso_min,
        "motivo": c.motivo,
        "solicitado_por": c.solicitado_por,
        "fecha_solicitud": c.fecha_solicitud.isoformat() + "Z" if c.fecha_solicitud else None,
        "estado": c.estado,
        "revisado_por": c.revisado_por,
        "fecha_revision": c.fecha_revision.isoformat() + "Z" if c.fecha_revision else None,
        "comentario_revision": c.comentario_revision,
        "created_at": c.created_at.isoformat() + "Z",
    }


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/", status_code=201)
def crear_correccion(data: CorreccionCreate, db: Session = Depends(get_db)):
    """
    Crear solicitud de corrección de fichaje.
    Crea automáticamente un registro en aprobaciones_log.
    """
    # Verificar fichaje
    fichaje = db.query(Fichaje).filter(Fichaje.id == data.fichaje_id).first()
    if not fichaje:
        raise HTTPException(404, "Fichaje no encontrado")

    # Verificar empleado
    empleado = db.query(Empleado).filter(Empleado.id == data.empleado_id).first()
    if not empleado:
        raise HTTPException(404, "Empleado no encontrado")

    # Crear solicitud con snapshot de los valores actuales
    solicitud = SolicitudCorreccion(
        fichaje_id=data.fichaje_id,
        empleado_id=data.empleado_id,
        original_entrada=fichaje.fecha_entrada,
        original_salida=fichaje.fecha_salida,
        original_descanso_min=fichaje.minutos_descanso,
        solicitada_entrada=data.solicitada_entrada,
        solicitada_salida=data.solicitada_salida,
        solicitado_descanso_min=data.solicitado_descanso_min,
        motivo=data.motivo,
        solicitado_por=data.solicitado_por,
        fecha_solicitud=datetime.utcnow(),
        estado="PENDIENTE",
    )
    db.add(solicitud)
    db.flush()  # Para obtener el ID

    # Crear registro en aprobaciones_log automáticamente
    aprobacion = AprobacionLog(
        tipo_entidad=TIPO_ENTIDAD,
        entidad_id=str(solicitud.id),
        nivel1_accion="PENDIENTE",
        estado_final="PENDIENTE",
    )
    db.add(aprobacion)
    db.commit()
    db.refresh(solicitud)

    return {
        **_correc_dict(solicitud, empleado),
        "aprobacion_url": f"/api/v1/aprobaciones/{TIPO_ENTIDAD}/{solicitud.id}",
    }


@router.get("/")
def listar_correcciones(
    empleado_id: Optional[UUID] = None,
    estado: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Lista solicitudes de corrección con filtros."""
    q = db.query(SolicitudCorreccion)
    if empleado_id:
        q = q.filter(SolicitudCorreccion.empleado_id == empleado_id)
    if estado:
        q = q.filter(SolicitudCorreccion.estado == estado.upper())

    total = q.count()
    correcciones = (
        q.order_by(SolicitudCorreccion.fecha_solicitud.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "data": [_correc_dict(c) for c in correcciones],
        "pagination": {"page": page, "limit": limit, "total": total},
    }


@router.get("/{correccion_id}")
def ver_correccion(correccion_id: UUID, db: Session = Depends(get_db)):
    """Detalle de una solicitud de corrección + estado de aprobación."""
    c = db.query(SolicitudCorreccion).filter(
        SolicitudCorreccion.id == correccion_id
    ).first()
    if not c:
        raise HTTPException(404, "Solicitud de corrección no encontrada")

    # Buscar estado de aprobación
    aprobacion = db.query(AprobacionLog).filter(
        AprobacionLog.tipo_entidad == TIPO_ENTIDAD,
        AprobacionLog.entidad_id == str(correccion_id),
    ).first()

    result = _correc_dict(c)
    result["aprobacion"] = {
        "estado_final": aprobacion.estado_final if aprobacion else None,
        "nivel1_accion": aprobacion.nivel1_accion if aprobacion else None,
        "nivel1_usuario": aprobacion.nivel1_usuario if aprobacion else None,
        "nivel2_accion": aprobacion.nivel2_accion if aprobacion else None,
        "nivel2_usuario": aprobacion.nivel2_usuario if aprobacion else None,
    }
    return result
