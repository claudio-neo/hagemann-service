"""
API de Modelos de Turno (HG-14) y Planificación de Turnos (HG-15)
"""
from datetime import datetime, time, date, timedelta
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.turno import ModeloTurno, PlanTurno
from ..models.empleado import Empleado, Grupo

router = APIRouter(prefix="/turnos", tags=["Turnos"])


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _calc_horas_netas(
    hora_inicio: Optional[time],
    hora_fin: Optional[time],
    minutos_pausa: int,
    cruza_medianoche: bool,
) -> float:
    """Calcula horas netas trabajadas descontando la pausa."""
    if hora_inicio is None or hora_fin is None:
        return 0.0
    start_m = hora_inicio.hour * 60 + hora_inicio.minute
    end_m = hora_fin.hour * 60 + hora_fin.minute
    if cruza_medianoche:
        end_m += 1440  # +24h
    netos = max(0, end_m - start_m - minutos_pausa)
    return round(netos / 60, 2)


def _modelo_dict(m: ModeloTurno) -> dict:
    return {
        "id": str(m.id),
        "nombre": m.nombre,
        "codigo": m.codigo,
        "hora_inicio": m.hora_inicio.strftime("%H:%M") if m.hora_inicio else None,
        "hora_fin": m.hora_fin.strftime("%H:%M") if m.hora_fin else None,
        "minutos_pausa": m.minutos_pausa,
        "horas_netas": m.horas_netas,
        "cruza_medianoche": m.cruza_medianoche,
        "color": m.color,
        "activo": m.activo,
        "created_by": m.created_by,
        "created_at": m.created_at.isoformat() + "Z",
    }


def _plan_dict(p: PlanTurno, empleado: Empleado = None, modelo: ModeloTurno = None) -> dict:
    emp = empleado or p.empleado
    mod = modelo or p.modelo_turno
    return {
        "id": str(p.id),
        "empleado_id": str(p.empleado_id),
        "empleado_nombre": f"{emp.nombre} {emp.apellido or ''}".strip() if emp else None,
        "empleado_id_nummer": emp.id_nummer if emp else None,
        "modelo_turno_id": str(p.modelo_turno_id) if p.modelo_turno_id else None,
        "modelo_codigo": mod.codigo if mod else None,
        "modelo_nombre": mod.nombre if mod else None,
        "fecha_plan": p.fecha_plan.isoformat(),
        "entrada_real": p.entrada_real.isoformat() + "Z" if p.entrada_real else None,
        "salida_real": p.salida_real.isoformat() + "Z" if p.salida_real else None,
        "estado": p.estado,
        "estado_nombre": {0: "Planificado", 1: "Cumplido", 2: "Ausente", 3: "Modificado"}.get(p.estado, "?"),
        "tipo_ausencia": p.tipo_ausencia,
        "nota": p.nota,
        "created_by": p.created_by,
        "created_at": p.created_at.isoformat() + "Z",
    }


# ─── Schemas ─────────────────────────────────────────────────────────────────

class ModeloTurnoCreate(BaseModel):
    nombre: str
    codigo: str
    hora_inicio: Optional[str] = None   # "HH:MM"
    hora_fin: Optional[str] = None      # "HH:MM"
    minutos_pausa: int = 0
    cruza_medianoche: bool = False
    color: Optional[str] = "#607D8B"
    created_by: Optional[str] = None

class ModeloTurnoUpdate(BaseModel):
    nombre: Optional[str] = None
    hora_inicio: Optional[str] = None
    hora_fin: Optional[str] = None
    minutos_pausa: Optional[int] = None
    cruza_medianoche: Optional[bool] = None
    color: Optional[str] = None
    activo: Optional[bool] = None

class PlanTurnoCreate(BaseModel):
    empleado_id: UUID
    modelo_turno_id: Optional[UUID] = None
    fecha_plan: date
    nota: Optional[str] = None
    created_by: Optional[str] = None

class PlanTurnoBulk(BaseModel):
    """Asignación masiva: N empleados × M fechas × 1 modelo"""
    empleado_ids: List[UUID]
    fechas: List[date]
    modelo_turno_id: Optional[UUID] = None
    nota: Optional[str] = None
    created_by: Optional[str] = None

class PlanTurnoUpdate(BaseModel):
    modelo_turno_id: Optional[UUID] = None
    entrada_real: Optional[datetime] = None
    salida_real: Optional[datetime] = None
    estado: Optional[int] = None
    tipo_ausencia: Optional[str] = None
    nota: Optional[str] = None


def _parse_time(s: Optional[str]) -> Optional[time]:
    if not s:
        return None
    try:
        parts = s.split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        raise HTTPException(400, f"Formato de hora inválido: '{s}'. Use HH:MM")


# ─── Endpoints: Modelos de Turno (HG-14) ─────────────────────────────────────

@router.get("/modelos")
def listar_modelos(
    activo: Optional[bool] = True,
    db: Session = Depends(get_db),
):
    """Lista modelos de turno. Por defecto solo activos."""
    q = db.query(ModeloTurno)
    if activo is not None:
        q = q.filter(ModeloTurno.activo == activo)
    modelos = q.order_by(ModeloTurno.nombre).all()
    return {"data": [_modelo_dict(m) for m in modelos], "total": len(modelos)}


@router.post("/modelos", status_code=201)
def crear_modelo(data: ModeloTurnoCreate, db: Session = Depends(get_db)):
    """Crear un nuevo modelo de turno. Calcula horas_netas automáticamente."""
    # Verificar código único
    if db.query(ModeloTurno).filter(ModeloTurno.codigo == data.codigo).first():
        raise HTTPException(409, f"Ya existe un modelo con código '{data.codigo}'")

    hi = _parse_time(data.hora_inicio)
    hf = _parse_time(data.hora_fin)
    horas_netas = _calc_horas_netas(hi, hf, data.minutos_pausa, data.cruza_medianoche)

    modelo = ModeloTurno(
        nombre=data.nombre,
        codigo=data.codigo,
        hora_inicio=hi,
        hora_fin=hf,
        minutos_pausa=data.minutos_pausa,
        horas_netas=horas_netas,
        cruza_medianoche=data.cruza_medianoche,
        color=data.color,
        created_by=data.created_by,
    )
    db.add(modelo)
    db.commit()
    db.refresh(modelo)
    return _modelo_dict(modelo)


@router.put("/modelos/{modelo_id}")
def editar_modelo(
    modelo_id: UUID,
    data: ModeloTurnoUpdate,
    db: Session = Depends(get_db),
):
    """Editar un modelo de turno. Recalcula horas_netas si se cambian horas."""
    modelo = db.query(ModeloTurno).filter(ModeloTurno.id == modelo_id).first()
    if not modelo:
        raise HTTPException(404, "Modelo de turno no encontrado")

    if data.nombre is not None:
        modelo.nombre = data.nombre
    if data.color is not None:
        modelo.color = data.color
    if data.activo is not None:
        modelo.activo = data.activo

    # Si se cambian horas o pausa, recalcular
    recalcular = False
    if data.hora_inicio is not None:
        modelo.hora_inicio = _parse_time(data.hora_inicio)
        recalcular = True
    if data.hora_fin is not None:
        modelo.hora_fin = _parse_time(data.hora_fin)
        recalcular = True
    if data.minutos_pausa is not None:
        modelo.minutos_pausa = data.minutos_pausa
        recalcular = True
    if data.cruza_medianoche is not None:
        modelo.cruza_medianoche = data.cruza_medianoche
        recalcular = True

    if recalcular:
        modelo.horas_netas = _calc_horas_netas(
            modelo.hora_inicio, modelo.hora_fin,
            modelo.minutos_pausa, modelo.cruza_medianoche,
        )

    modelo.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(modelo)
    return _modelo_dict(modelo)


@router.delete("/modelos/{modelo_id}")
def eliminar_modelo(modelo_id: UUID, db: Session = Depends(get_db)):
    """Soft-delete: marca el modelo como inactivo."""
    modelo = db.query(ModeloTurno).filter(ModeloTurno.id == modelo_id).first()
    if not modelo:
        raise HTTPException(404, "Modelo de turno no encontrado")
    modelo.activo = False
    modelo.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "message": f"Modelo '{modelo.nombre}' desactivado"}


# ─── Endpoints: Planes de Turno (HG-15) ──────────────────────────────────────

@router.get("/planes")
def listar_planes(
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    grupo_id: Optional[UUID] = None,
    empleado_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Lista planes de turno con filtros."""
    q = (
        db.query(PlanTurno)
        .join(Empleado, PlanTurno.empleado_id == Empleado.id)
    )
    if empleado_id:
        q = q.filter(PlanTurno.empleado_id == empleado_id)
    if grupo_id:
        q = q.filter(Empleado.grupo_id == grupo_id)
    if desde:
        q = q.filter(PlanTurno.fecha_plan >= desde)
    if hasta:
        q = q.filter(PlanTurno.fecha_plan <= hasta)

    total = q.count()
    planes = (
        q.order_by(PlanTurno.fecha_plan, Empleado.nombre)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "data": [_plan_dict(p) for p in planes],
        "pagination": {"page": page, "limit": limit, "total": total},
    }


@router.post("/planes", status_code=201)
def crear_plan(data: PlanTurnoCreate, db: Session = Depends(get_db)):
    """Asignar turno individual a un empleado en una fecha."""
    # Verificar empleado
    emp = db.query(Empleado).filter(Empleado.id == data.empleado_id).first()
    if not emp:
        raise HTTPException(404, "Mitarbeiter nicht gefunden")

    # Verificar modelo si se especificó
    if data.modelo_turno_id:
        modelo = db.query(ModeloTurno).filter(
            ModeloTurno.id == data.modelo_turno_id
        ).first()
        if not modelo:
            raise HTTPException(404, "Modelo de turno no encontrado")

    # Verificar duplicado
    existe = db.query(PlanTurno).filter(
        PlanTurno.empleado_id == data.empleado_id,
        PlanTurno.fecha_plan == data.fecha_plan,
    ).first()
    if existe:
        raise HTTPException(
            409,
            f"Ya existe un plan para empleado {emp.nombre} en fecha {data.fecha_plan}"
        )

    plan = PlanTurno(
        empleado_id=data.empleado_id,
        modelo_turno_id=data.modelo_turno_id,
        fecha_plan=data.fecha_plan,
        nota=data.nota,
        created_by=data.created_by,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return _plan_dict(plan)


@router.post("/planes/bulk")
def crear_planes_bulk(data: PlanTurnoBulk, db: Session = Depends(get_db)):
    """
    Asignación masiva: lista de empleados × lista de fechas × un modelo.
    Si existiert bereits plan para (empleado, fecha) se omite (no error).
    """
    if not data.empleado_ids or not data.fechas:
        raise HTTPException(400, "empleado_ids y fechas no pueden estar vacíos")

    creados = 0
    omitidos = 0
    errores = []

    for emp_id in data.empleado_ids:
        emp = db.query(Empleado).filter(Empleado.id == emp_id).first()
        if not emp:
            errores.append(f"Empleado {emp_id} no encontrado")
            continue

        for fecha in data.fechas:
            existe = db.query(PlanTurno).filter(
                PlanTurno.empleado_id == emp_id,
                PlanTurno.fecha_plan == fecha,
            ).first()
            if existe:
                omitidos += 1
                continue

            plan = PlanTurno(
                empleado_id=emp_id,
                modelo_turno_id=data.modelo_turno_id,
                fecha_plan=fecha,
                nota=data.nota,
                created_by=data.created_by,
            )
            db.add(plan)
            creados += 1

    db.commit()
    return {
        "ok": True,
        "creados": creados,
        "omitidos": omitidos,
        "errores": errores,
    }


@router.put("/planes/{plan_id}")
def editar_plan(plan_id: UUID, data: PlanTurnoUpdate, db: Session = Depends(get_db)):
    """Actualizar plan de turno (cambiar modelo, estado, registrar real, etc.)"""
    plan = db.query(PlanTurno).filter(PlanTurno.id == plan_id).first()
    if not plan:
        raise HTTPException(404, "Plan de turno no encontrado")

    if data.modelo_turno_id is not None:
        plan.modelo_turno_id = data.modelo_turno_id
    if data.entrada_real is not None:
        plan.entrada_real = data.entrada_real
    if data.salida_real is not None:
        plan.salida_real = data.salida_real
    if data.estado is not None:
        plan.estado = data.estado
    if data.tipo_ausencia is not None:
        plan.tipo_ausencia = data.tipo_ausencia
    if data.nota is not None:
        plan.nota = data.nota

    plan.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(plan)
    return _plan_dict(plan)


@router.delete("/planes/{plan_id}")
def eliminar_plan(plan_id: UUID, db: Session = Depends(get_db)):
    """Eliminar un plan de turno."""
    plan = db.query(PlanTurno).filter(PlanTurno.id == plan_id).first()
    if not plan:
        raise HTTPException(404, "Plan de turno no encontrado")
    db.delete(plan)
    db.commit()
    return {"ok": True, "message": "Plan eliminado"}


# ─── Seed: modelos de turno iniciales ────────────────────────────────────────

def seed_modelos_turno(db: Session):
    """Crear los 5 modelos de turno estándar si no existen."""
    seeds = [
        {
            "nombre": "Frühschicht",
            "codigo": "F",
            "hora_inicio": "06:00",
            "hora_fin": "14:00",
            "minutos_pausa": 30,
            "cruza_medianoche": False,
            "color": "#FFC107",
        },
        {
            "nombre": "Spätschicht",
            "codigo": "S",
            "hora_inicio": "14:00",
            "hora_fin": "22:00",
            "minutos_pausa": 30,
            "cruza_medianoche": False,
            "color": "#FF5722",
        },
        {
            "nombre": "Nachtschicht",
            "codigo": "N",
            "hora_inicio": "22:00",
            "hora_fin": "06:00",
            "minutos_pausa": 30,
            "cruza_medianoche": True,
            "color": "#3F51B5",
        },
        {
            "nombre": "Normalschicht",
            "codigo": "NS",
            "hora_inicio": "08:00",
            "hora_fin": "16:30",
            "minutos_pausa": 30,
            "cruza_medianoche": False,
            "color": "#4CAF50",
        },
        {
            "nombre": "Frei",
            "codigo": "X",
            "hora_inicio": None,
            "hora_fin": None,
            "minutos_pausa": 0,
            "cruza_medianoche": False,
            "color": "#9E9E9E",
        },
    ]
    for s in seeds:
        if not db.query(ModeloTurno).filter(ModeloTurno.codigo == s["codigo"]).first():
            hi = _parse_time_safe(s["hora_inicio"])
            hf = _parse_time_safe(s["hora_fin"])
            db.add(ModeloTurno(
                nombre=s["nombre"],
                codigo=s["codigo"],
                hora_inicio=hi,
                hora_fin=hf,
                minutos_pausa=s["minutos_pausa"],
                horas_netas=_calc_horas_netas(hi, hf, s["minutos_pausa"], s["cruza_medianoche"]),
                cruza_medianoche=s["cruza_medianoche"],
                color=s["color"],
                created_by="seed",
            ))
    db.commit()


def _parse_time_safe(s: Optional[str]) -> Optional[time]:
    if not s:
        return None
    parts = s.split(":")
    return time(int(parts[0]), int(parts[1]))
