"""
API de Vacaciones y Ausencias — Hagemann
Workflow 2 niveles: Abteilungsleiter propone → Admin aprueba/rechaza
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, func, extract
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import date, datetime, timedelta

from ..database import get_db
from ..models.empleado import Empleado
from ..models.vacaciones import (
    PeriodoVacaciones, SolicitudVacaciones, LimiteVacaciones,
    TipoAusencia, EstadoSolicitud,
)
from ..models.fichaje import Fichaje  # noqa — needed for backref init

router = APIRouter(prefix="/vacaciones", tags=["Vacaciones"])


# ========== HELPERS ==========

def _calcular_dias_laborables(
    fecha_inicio: date, fecha_fin: date, db: Session
) -> int:
    """
    Cuenta días laborables entre dos fechas (inclusive).
    Excluye sábados, domingos y festivos activos (DE + SN).
    """
    from ..models.vacaciones import Festivo

    # Obtener festivos en el rango
    festivos = db.query(Festivo.fecha).filter(
        Festivo.activo == True,
        Festivo.fecha >= fecha_inicio,
        Festivo.fecha <= fecha_fin,
        Festivo.bundesland.in_(["DE", "SN"]),
    ).all()
    festivos_set = {f.fecha for f in festivos}

    dias = 0
    current = fecha_inicio
    while current <= fecha_fin:
        if current.weekday() < 5 and current not in festivos_set:  # lun-vie y no festivo
            dias += 1
        current += timedelta(days=1)
    return dias


def _get_periodo(db: Session, empleado_id: UUID, anio: int) -> Optional[PeriodoVacaciones]:
    return db.query(PeriodoVacaciones).filter(
        PeriodoVacaciones.empleado_id == empleado_id,
        PeriodoVacaciones.anio == anio,
    ).first()


def _recalcular_dias_usados(db: Session, periodo_id: UUID) -> int:
    """Recalcula dias_usados sumando solicitudes APROBADAS."""
    total = db.query(func.coalesce(func.sum(SolicitudVacaciones.dias), 0)).filter(
        SolicitudVacaciones.periodo_id == periodo_id,
        SolicitudVacaciones.estado == EstadoSolicitud.APROBADA,
    ).scalar()
    return int(total)


# ========== SCHEMAS ==========

class PeriodoCreate(BaseModel):
    empleado_id: UUID
    anio: int
    dias_contrato: int = 30
    dias_extra: int = 0
    notas: Optional[str] = None


class SolicitudCreate(BaseModel):
    empleado_id: UUID
    anio: int
    fecha_inicio: date
    fecha_fin: date
    tipo_ausencia: TipoAusencia = TipoAusencia.VACACIONES
    notas: Optional[str] = None


class AccionNivel1(BaseModel):
    """Abteilungsleiter propone (o rechaza)"""
    aprobado_por: str
    aprobar: bool
    notas: Optional[str] = None


class AccionNivel2(BaseModel):
    """Admin aprueba o rechaza definitivamente"""
    aprobado_por: str
    aprobar: bool
    notas: Optional[str] = None
    motivo_rechazo: Optional[str] = None


class LimiteCreate(BaseModel):
    grupo_id: UUID
    fecha_inicio: date
    fecha_fin: date
    max_ausencias: int = 1
    descripcion: Optional[str] = None


# ========== SERIALIZERS ==========

def _periodo_dict(p: PeriodoVacaciones) -> dict:
    disponibles = (p.dias_contrato + p.dias_extra) - p.dias_usados
    return {
        "id": str(p.id),
        "empleado_id": str(p.empleado_id),
        "anio": p.anio,
        "dias_contrato": p.dias_contrato,
        "dias_extra": p.dias_extra,
        "dias_totales": p.dias_contrato + p.dias_extra,
        "dias_usados": p.dias_usados,
        "dias_disponibles": max(0, disponibles),
        "notas": p.notas,
    }


def _solicitud_dict(s: SolicitudVacaciones) -> dict:
    return {
        "id": str(s.id),
        "empleado_id": str(s.empleado_id),
        "empleado_nombre": (
            f"{s.empleado.nombre} {s.empleado.apellido or ''}".strip()
            if s.empleado else None
        ),
        "periodo_id": str(s.periodo_id),
        "fecha_inicio": s.fecha_inicio.isoformat(),
        "fecha_fin": s.fecha_fin.isoformat(),
        "dias": s.dias,
        "tipo_ausencia": s.tipo_ausencia,
        "estado": s.estado,
        "aprobado_por_nivel1": s.aprobado_por_nivel1,
        "fecha_nivel1": s.fecha_nivel1.isoformat() if s.fecha_nivel1 else None,
        "notas_nivel1": s.notas_nivel1,
        "aprobado_por_nivel2": s.aprobado_por_nivel2,
        "fecha_nivel2": s.fecha_nivel2.isoformat() if s.fecha_nivel2 else None,
        "notas_nivel2": s.notas_nivel2,
        "motivo_rechazo": s.motivo_rechazo,
        "notas": s.notas,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# ========== ENDPOINTS: PERIODOS ==========

@router.post("/periodos", status_code=201)
def crear_periodo(data: PeriodoCreate, db: Session = Depends(get_db)):
    """Crea un periodo vacacional para un empleado."""
    emp = db.query(Empleado).filter(Empleado.id == data.empleado_id).first()
    if not emp:
        raise HTTPException(404, "Empleado no encontrado")

    existing = _get_periodo(db, data.empleado_id, data.anio)
    if existing:
        raise HTTPException(409, f"Ya existe un periodo para {data.anio}")

    periodo = PeriodoVacaciones(
        empleado_id=data.empleado_id,
        anio=data.anio,
        dias_contrato=data.dias_contrato,
        dias_extra=data.dias_extra,
        notas=data.notas,
    )
    db.add(periodo)
    db.commit()
    db.refresh(periodo)
    return {"message": "Periodo creado", **_periodo_dict(periodo)}


@router.get("/periodos/{empleado_id}")
def listar_periodos_empleado(
    empleado_id: UUID, db: Session = Depends(get_db)
):
    """Lista todos los periodos de un empleado."""
    periodos = db.query(PeriodoVacaciones).filter(
        PeriodoVacaciones.empleado_id == empleado_id
    ).order_by(PeriodoVacaciones.anio.desc()).all()
    return {"data": [_periodo_dict(p) for p in periodos], "total": len(periodos)}


# ========== ENDPOINTS: SALDO ==========

@router.get("/saldo/{empleado_id}")
def saldo_vacaciones(
    empleado_id: UUID,
    anio: int = Query(..., description="Año (ej: 2026)"),
    db: Session = Depends(get_db),
):
    """
    Devuelve el saldo de vacaciones de un empleado para un año.
    - dias_usados: suma de solicitudes APROBADAS
    - dias_pendientes: suma de solicitudes PENDIENTES o PROPUESTAS
    - dias_disponibles: totales - usados - pendientes
    """
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Empleado no encontrado")

    periodo = _get_periodo(db, empleado_id, anio)
    if not periodo:
        raise HTTPException(
            404,
            f"No hay periodo vacacional para {emp.nombre} en {anio}. "
            f"Créalo primero con POST /vacaciones/periodos"
        )

    # Recalcular en tiempo real
    dias_aprobados = _recalcular_dias_usados(db, periodo.id)

    dias_pendientes = db.query(func.coalesce(func.sum(SolicitudVacaciones.dias), 0)).filter(
        SolicitudVacaciones.periodo_id == periodo.id,
        SolicitudVacaciones.estado.in_([EstadoSolicitud.PENDIENTE, EstadoSolicitud.PROPUESTA]),
    ).scalar() or 0

    dias_totales = periodo.dias_contrato + periodo.dias_extra
    dias_disponibles = max(0, dias_totales - dias_aprobados - int(dias_pendientes))

    # Actualizar campo cached
    if periodo.dias_usados != dias_aprobados:
        periodo.dias_usados = dias_aprobados
        db.commit()

    return {
        "empleado": {
            "id": str(emp.id),
            "nombre": f"{emp.nombre} {emp.apellido or ''}".strip(),
        },
        "anio": anio,
        "dias_contrato": periodo.dias_contrato,
        "dias_extra": periodo.dias_extra,
        "dias_totales": dias_totales,
        "dias_usados": dias_aprobados,
        "dias_pendientes": int(dias_pendientes),
        "dias_disponibles": dias_disponibles,
    }


# ========== ENDPOINTS: SOLICITUDES ==========

@router.post("/solicitudes", status_code=201)
def crear_solicitud(data: SolicitudCreate, db: Session = Depends(get_db)):
    """
    Crea una solicitud de vacaciones/ausencia.
    El empleado la crea → queda en estado PENDIENTE.
    Calcula automáticamente los días laborables (excluye fines de semana y festivos).
    """
    emp = db.query(Empleado).filter(Empleado.id == data.empleado_id).first()
    if not emp:
        raise HTTPException(404, "Empleado no encontrado")

    if data.fecha_fin < data.fecha_inicio:
        raise HTTPException(400, "fecha_fin debe ser >= fecha_inicio")

    periodo = _get_periodo(db, data.empleado_id, data.anio)
    if not periodo:
        raise HTTPException(
            404,
            f"No hay periodo vacacional para {data.anio}. "
            f"Créalo primero con POST /vacaciones/periodos"
        )

    # Calcular días laborables
    dias = _calcular_dias_laborables(data.fecha_inicio, data.fecha_fin, db)
    if dias <= 0:
        raise HTTPException(400, "No hay días laborables en el rango seleccionado")

    # Verificar saldo disponible (solo para VACACIONES y ASUNTOS_PROPIOS)
    if data.tipo_ausencia in (TipoAusencia.VACACIONES, TipoAusencia.ASUNTOS_PROPIOS):
        dias_aprobados = _recalcular_dias_usados(db, periodo.id)
        dias_comprometidos = db.query(
            func.coalesce(func.sum(SolicitudVacaciones.dias), 0)
        ).filter(
            SolicitudVacaciones.periodo_id == periodo.id,
            SolicitudVacaciones.estado.in_([
                EstadoSolicitud.PENDIENTE, EstadoSolicitud.PROPUESTA
            ]),
        ).scalar() or 0

        dias_disponibles = (periodo.dias_contrato + periodo.dias_extra) - dias_aprobados - int(dias_comprometidos)
        if dias > dias_disponibles:
            raise HTTPException(
                422,
                f"Saldo insuficiente: solicitas {dias} días, disponibles {dias_disponibles}"
            )

    # Verificar solapamiento con otras solicitudes aprobadas/pendientes
    solapamiento = db.query(SolicitudVacaciones).filter(
        SolicitudVacaciones.empleado_id == data.empleado_id,
        SolicitudVacaciones.estado.in_([
            EstadoSolicitud.PENDIENTE, EstadoSolicitud.PROPUESTA, EstadoSolicitud.APROBADA
        ]),
        SolicitudVacaciones.fecha_inicio <= data.fecha_fin,
        SolicitudVacaciones.fecha_fin >= data.fecha_inicio,
    ).first()
    if solapamiento:
        raise HTTPException(
            409,
            f"Se solapa con solicitud existente {solapamiento.id} "
            f"({solapamiento.fecha_inicio}–{solapamiento.fecha_fin}, estado={solapamiento.estado})"
        )

    solicitud = SolicitudVacaciones(
        empleado_id=data.empleado_id,
        periodo_id=periodo.id,
        fecha_inicio=data.fecha_inicio,
        fecha_fin=data.fecha_fin,
        dias=dias,
        tipo_ausencia=data.tipo_ausencia,
        estado=EstadoSolicitud.PENDIENTE,
        notas=data.notas,
    )
    db.add(solicitud)
    db.commit()
    db.refresh(solicitud)

    # Cargar relación empleado para serializar
    solicitud.empleado = emp

    return {
        "message": f"Solicitud creada — {dias} días laborables",
        **_solicitud_dict(solicitud),
    }


@router.get("/solicitudes")
def listar_solicitudes(
    empleado_id: Optional[UUID] = None,
    estado: Optional[EstadoSolicitud] = None,
    anio: Optional[int] = None,
    tipo_ausencia: Optional[TipoAusencia] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Lista solicitudes con filtros."""
    query = db.query(SolicitudVacaciones).options(
        joinedload(SolicitudVacaciones.empleado)
    )
    if empleado_id:
        query = query.filter(SolicitudVacaciones.empleado_id == empleado_id)
    if estado:
        query = query.filter(SolicitudVacaciones.estado == estado)
    if tipo_ausencia:
        query = query.filter(SolicitudVacaciones.tipo_ausencia == tipo_ausencia)
    if anio:
        query = query.join(PeriodoVacaciones).filter(PeriodoVacaciones.anio == anio)

    total = query.count()
    solicitudes = (
        query.order_by(SolicitudVacaciones.fecha_inicio.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return {
        "data": [_solicitud_dict(s) for s in solicitudes],
        "pagination": {"page": page, "limit": limit, "total": total},
    }


@router.get("/solicitudes/{solicitud_id}")
def obtener_solicitud(solicitud_id: UUID, db: Session = Depends(get_db)):
    """Obtiene una solicitud por ID."""
    s = db.query(SolicitudVacaciones).options(
        joinedload(SolicitudVacaciones.empleado)
    ).filter(SolicitudVacaciones.id == solicitud_id).first()
    if not s:
        raise HTTPException(404, "Solicitud no encontrada")
    return _solicitud_dict(s)


@router.post("/solicitudes/{solicitud_id}/nivel1")
def accion_nivel1(
    solicitud_id: UUID,
    data: AccionNivel1,
    db: Session = Depends(get_db),
):
    """
    Acción del Abteilungsleiter (nivel 1).
    - aprobar=True → estado PROPUESTA
    - aprobar=False → estado RECHAZADA
    """
    s = db.query(SolicitudVacaciones).options(
        joinedload(SolicitudVacaciones.empleado)
    ).filter(SolicitudVacaciones.id == solicitud_id).first()
    if not s:
        raise HTTPException(404, "Solicitud no encontrada")
    if s.estado != EstadoSolicitud.PENDIENTE:
        raise HTTPException(
            409,
            f"La solicitud está en estado '{s.estado}', se esperaba PENDIENTE"
        )

    if data.aprobar:
        s.estado = EstadoSolicitud.PROPUESTA
        s.aprobado_por_nivel1 = data.aprobado_por
        s.fecha_nivel1 = datetime.utcnow()
        s.notas_nivel1 = data.notas
        msg = f"Solicitud propuesta por {data.aprobado_por}"
    else:
        s.estado = EstadoSolicitud.RECHAZADA
        s.aprobado_por_nivel1 = data.aprobado_por
        s.fecha_nivel1 = datetime.utcnow()
        s.notas_nivel1 = data.notas
        s.motivo_rechazo = data.notas
        msg = f"Solicitud rechazada por {data.aprobado_por}"

    db.commit()
    db.refresh(s)
    return {"message": msg, **_solicitud_dict(s)}


@router.post("/solicitudes/{solicitud_id}/nivel2")
def accion_nivel2(
    solicitud_id: UUID,
    data: AccionNivel2,
    db: Session = Depends(get_db),
):
    """
    Acción del Admin (nivel 2).
    - aprobar=True → estado APROBADA, descuenta días del periodo
    - aprobar=False → estado RECHAZADA
    Solo puede actuar sobre solicitudes en estado PROPUESTA.
    """
    s = db.query(SolicitudVacaciones).options(
        joinedload(SolicitudVacaciones.empleado)
    ).filter(SolicitudVacaciones.id == solicitud_id).first()
    if not s:
        raise HTTPException(404, "Solicitud no encontrada")
    if s.estado != EstadoSolicitud.PROPUESTA:
        raise HTTPException(
            409,
            f"La solicitud está en estado '{s.estado}', se esperaba PROPUESTA"
        )

    s.aprobado_por_nivel2 = data.aprobado_por
    s.fecha_nivel2 = datetime.utcnow()
    s.notas_nivel2 = data.notas

    if data.aprobar:
        s.estado = EstadoSolicitud.APROBADA
        msg = f"Solicitud aprobada por {data.aprobado_por}"

        # Actualizar dias_usados del periodo
        periodo = db.query(PeriodoVacaciones).filter(
            PeriodoVacaciones.id == s.periodo_id
        ).first()
        if periodo:
            periodo.dias_usados = _recalcular_dias_usados(db, periodo.id) + s.dias
    else:
        s.estado = EstadoSolicitud.RECHAZADA
        s.motivo_rechazo = data.motivo_rechazo or data.notas
        msg = f"Solicitud rechazada por {data.aprobado_por}"

    db.commit()
    db.refresh(s)
    return {"message": msg, **_solicitud_dict(s)}


@router.post("/solicitudes/{solicitud_id}/cancelar")
def cancelar_solicitud(
    solicitud_id: UUID,
    motivo: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Cancela (rechaza) una solicitud PENDIENTE o PROPUESTA."""
    s = db.query(SolicitudVacaciones).filter(
        SolicitudVacaciones.id == solicitud_id
    ).first()
    if not s:
        raise HTTPException(404, "Solicitud no encontrada")
    if s.estado in (EstadoSolicitud.APROBADA, EstadoSolicitud.RECHAZADA):
        raise HTTPException(409, f"No se puede cancelar en estado '{s.estado}'")

    estado_anterior = s.estado
    s.estado = EstadoSolicitud.RECHAZADA
    s.motivo_rechazo = motivo or "Cancelada"
    db.commit()

    return {
        "message": f"Solicitud cancelada (antes: {estado_anterior})",
        "id": str(s.id),
    }


# ========== ENDPOINTS: LÍMITES ==========

@router.get("/limites")
def listar_limites(
    grupo_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Lista límites de ausencias por grupo."""
    query = db.query(LimiteVacaciones).options(
        joinedload(LimiteVacaciones.grupo)
    )
    if grupo_id:
        query = query.filter(LimiteVacaciones.grupo_id == grupo_id)
    limites = query.order_by(LimiteVacaciones.fecha_inicio).all()
    return {
        "data": [
            {
                "id": str(l.id),
                "grupo_id": str(l.grupo_id),
                "grupo_nombre": l.grupo.nombre if l.grupo else None,
                "fecha_inicio": l.fecha_inicio.isoformat(),
                "fecha_fin": l.fecha_fin.isoformat(),
                "max_ausencias": l.max_ausencias,
                "activo": l.activo,
                "descripcion": l.descripcion,
            }
            for l in limites
        ],
        "total": len(limites),
    }


@router.post("/limites", status_code=201)
def crear_limite(data: LimiteCreate, db: Session = Depends(get_db)):
    """Crea un límite de ausencias para un grupo."""
    limite = LimiteVacaciones(
        grupo_id=data.grupo_id,
        fecha_inicio=data.fecha_inicio,
        fecha_fin=data.fecha_fin,
        max_ausencias=data.max_ausencias,
        descripcion=data.descripcion,
    )
    db.add(limite)
    db.commit()
    db.refresh(limite)
    return {"id": str(limite.id), "message": "Límite creado"}
