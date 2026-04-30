"""
API de Reportes — doble vista empleado↔departamento + horas por tipo de turno
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import Optional
from uuid import UUID
from datetime import date, datetime

from ..database import get_db
from ..models.empleado import Empleado, CentroCoste
from ..models.fichaje import Fichaje, SegmentoTiempo
from ..models.turno import PlanTurno, ModeloTurno

router = APIRouter(prefix="/reportes", tags=["Reportes"])


@router.get("/horas-empleado")
def horas_por_empleado(
    empleado_id: UUID,
    desde: date = Query(...),
    hasta: date = Query(...),
    db: Session = Depends(get_db),
):
    """
    Horas de un empleado desglosadas por centro de coste.
    Vista: ¿Cuántas horas trabajó René y en qué departamentos?
    """
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Mitarbeiter nicht gefunden")

    desde_dt = datetime.combine(desde, datetime.min.time())
    hasta_dt = datetime.combine(hasta, datetime.max.time())

    # Total por centro de coste
    by_cc = (
        db.query(
            CentroCoste.id,
            CentroCoste.codigo,
            CentroCoste.nombre,
            func.coalesce(func.sum(SegmentoTiempo.minutos), 0).label("total_min"),
        )
        .join(SegmentoTiempo, SegmentoTiempo.centro_coste_id == CentroCoste.id)
        .filter(
            SegmentoTiempo.empleado_id == empleado_id,
            SegmentoTiempo.inicio >= desde_dt,
            SegmentoTiempo.fin <= hasta_dt,
            SegmentoTiempo.fin.isnot(None),
        )
        .group_by(CentroCoste.id, CentroCoste.codigo, CentroCoste.nombre)
        .all()
    )

    total_minutes = sum(r.total_min for r in by_cc)

    # Detalle diario
    daily = (
        db.query(
            func.date(SegmentoTiempo.inicio).label("dia"),
            CentroCoste.nombre.label("cc_nombre"),
            SegmentoTiempo.inicio,
            SegmentoTiempo.fin,
            SegmentoTiempo.minutos,
        )
        .join(CentroCoste, SegmentoTiempo.centro_coste_id == CentroCoste.id)
        .filter(
            SegmentoTiempo.empleado_id == empleado_id,
            SegmentoTiempo.inicio >= desde_dt,
            SegmentoTiempo.fin <= hasta_dt,
            SegmentoTiempo.fin.isnot(None),
        )
        .order_by(SegmentoTiempo.inicio)
        .all()
    )

    # Agrupar por día
    daily_grouped = {}
    for row in daily:
        day_key = str(row.dia)
        if day_key not in daily_grouped:
            daily_grouped[day_key] = {"fecha": day_key, "total_minutos": 0, "segmentos": []}
        daily_grouped[day_key]["segmentos"].append({
            "centro_coste": row.cc_nombre,
            "inicio": row.inicio.strftime("%H:%M"),
            "fin": row.fin.strftime("%H:%M") if row.fin else None,
            "minutos": row.minutos,
        })
        daily_grouped[day_key]["total_minutos"] += (row.minutos or 0)

    return {
        "empleado": {
            "id": str(emp.id),
            "id_nummer": emp.id_nummer,
            "nombre": f"{emp.nombre} {emp.apellido or ''}".strip(),
        },
        "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "total_minutos": total_minutes,
        "total_formateado": f"{total_minutes // 60}:{total_minutes % 60:02d}",
        "por_centro_coste": [
            {
                "centro_coste_id": str(r.id),
                "codigo": r.codigo,
                "nombre": r.nombre,
                "minutos": r.total_min,
                "formateado": f"{r.total_min // 60}:{r.total_min % 60:02d}",
                "porcentaje": round(r.total_min / total_minutes * 100, 1) if total_minutes else 0,
            }
            for r in by_cc
        ],
        "detalle_diario": list(daily_grouped.values()),
    }


@router.get("/horas-centro-coste")
def horas_por_centro_coste(
    centro_coste_id: UUID,
    desde: date = Query(...),
    hasta: date = Query(...),
    db: Session = Depends(get_db),
):
    """
    Horas de un departamento desglosadas por trabajador.
    Vista: ¿Cuántas horas consumió Logistik y de qué empleados?
    """
    cc = db.query(CentroCoste).filter(CentroCoste.id == centro_coste_id).first()
    if not cc:
        raise HTTPException(404, "Kostenstelle nicht gefunden")

    desde_dt = datetime.combine(desde, datetime.min.time())
    hasta_dt = datetime.combine(hasta, datetime.max.time())

    by_emp = (
        db.query(
            Empleado.id,
            Empleado.id_nummer,
            Empleado.nombre,
            Empleado.apellido,
            func.coalesce(func.sum(SegmentoTiempo.minutos), 0).label("total_min"),
        )
        .join(SegmentoTiempo, SegmentoTiempo.empleado_id == Empleado.id)
        .filter(
            SegmentoTiempo.centro_coste_id == centro_coste_id,
            SegmentoTiempo.inicio >= desde_dt,
            SegmentoTiempo.fin <= hasta_dt,
            SegmentoTiempo.fin.isnot(None),
        )
        .group_by(Empleado.id, Empleado.id_nummer, Empleado.nombre, Empleado.apellido)
        .order_by(func.sum(SegmentoTiempo.minutos).desc())
        .all()
    )

    total_minutes = sum(r.total_min for r in by_emp)

    return {
        "centro_coste": {
            "id": str(cc.id),
            "codigo": cc.codigo,
            "nombre": cc.nombre,
        },
        "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "total_minutos": total_minutes,
        "total_formateado": f"{total_minutes // 60}:{total_minutes % 60:02d}",
        "empleados_count": len(by_emp),
        "por_empleado": [
            {
                "empleado_id": str(r.id),
                "id_nummer": r.id_nummer,
                "nombre": f"{r.nombre} {r.apellido or ''}".strip(),
                "minutos": r.total_min,
                "formateado": f"{r.total_min // 60}:{r.total_min % 60:02d}",
                "porcentaje": round(r.total_min / total_minutes * 100, 1) if total_minutes else 0,
            }
            for r in by_emp
        ],
    }


@router.get("/resumen-centros-coste")
def resumen_centros_coste(
    desde: date = Query(...),
    hasta: date = Query(...),
    db: Session = Depends(get_db),
):
    """
    Vista gerencial: todos los centros de coste con total de horas y empleados.
    """
    desde_dt = datetime.combine(desde, datetime.min.time())
    hasta_dt = datetime.combine(hasta, datetime.max.time())

    results = (
        db.query(
            CentroCoste.id,
            CentroCoste.codigo,
            CentroCoste.nombre,
            CentroCoste.color,
            func.coalesce(func.sum(SegmentoTiempo.minutos), 0).label("total_min"),
            func.count(func.distinct(SegmentoTiempo.empleado_id)).label("emp_count"),
        )
        .outerjoin(
            SegmentoTiempo,
            and_(
                SegmentoTiempo.centro_coste_id == CentroCoste.id,
                SegmentoTiempo.inicio >= desde_dt,
                SegmentoTiempo.fin <= hasta_dt,
                SegmentoTiempo.fin.isnot(None),
            )
        )
        .filter(CentroCoste.activo == True)
        .group_by(CentroCoste.id, CentroCoste.codigo, CentroCoste.nombre, CentroCoste.color)
        .order_by(CentroCoste.codigo)
        .all()
    )

    grand_total = sum(r.total_min for r in results)

    return {
        "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "centros_coste": [
            {
                "id": str(r.id),
                "codigo": r.codigo,
                "nombre": r.nombre,
                "color": r.color,
                "total_minutos": r.total_min,
                "total_formateado": f"{r.total_min // 60}:{r.total_min % 60:02d}",
                "empleados_count": r.emp_count,
            }
            for r in results
        ],
        "total_minutos": grand_total,
        "total_formateado": f"{grand_total // 60}:{grand_total % 60:02d}",
    }


# ---------------------------------------------------------------------------
# HG-22: Horas por tipo de turno (Nachtzuschlag-Übersicht)
# ---------------------------------------------------------------------------

def _fmt(minutes: int) -> str:
    """Format minutes as H:MM"""
    h, m = divmod(abs(minutes), 60)
    return f"{h}:{m:02d}"


@router.get("/horas-por-turno")
def horas_por_turno(
    desde: date = Query(...),
    hasta: date = Query(...),
    empleado_id: Optional[UUID] = Query(None, description="Filtrar por empleado"),
    db: Session = Depends(get_db),
):
    """
    HG-22: Horas reales trabajadas por empleado desglosadas por tipo de turno.
    Cruza Fichaje (horas reales) con PlanTurno (modelo asignado ese día).

    Útil para calcular Nachtzuschläge, Frühschicht-Zulagen, etc.
    """
    desde_dt = datetime.combine(desde, datetime.min.time())
    hasta_dt = datetime.combine(hasta, datetime.max.time())

    # Cargar todos los modelos de turno (para el resumen de columnas)
    modelos = db.query(ModeloTurno).filter(ModeloTurno.activo == True).order_by(ModeloTurno.codigo).all()
    modelo_map = {str(m.id): m for m in modelos}

    # Cargar fichajes cerrados del periodo
    q = db.query(Fichaje).filter(
        Fichaje.fecha_entrada >= desde_dt,
        Fichaje.fecha_entrada <= hasta_dt,
        Fichaje.fecha_salida.isnot(None),
    )
    if empleado_id:
        q = q.filter(Fichaje.empleado_id == empleado_id)
    fichajes = q.order_by(Fichaje.empleado_id, Fichaje.fecha_entrada).all()

    if not fichajes:
        return {
            "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
            "modelos": [{"id": str(m.id), "codigo": m.codigo, "nombre": m.nombre, "color": m.color} for m in modelos],
            "empleados": [],
            "totales": {},
        }

    # Cargar planes del periodo (bulk, luego indexar en Python)
    planes_q = db.query(PlanTurno).filter(
        PlanTurno.fecha_plan >= desde,
        PlanTurno.fecha_plan <= hasta,
    )
    if empleado_id:
        planes_q = planes_q.filter(PlanTurno.empleado_id == empleado_id)
    planes = planes_q.all()
    # Index: (empleado_id_str, fecha_iso) → PlanTurno
    plan_idx = {(str(p.empleado_id), p.fecha_plan.isoformat()): p for p in planes}

    # Cargar datos de empleados
    emp_ids = list({f.empleado_id for f in fichajes})
    emps = db.query(Empleado).filter(Empleado.id.in_(emp_ids)).all()
    emp_map = {str(e.id): e for e in emps}

    # Agrupar por empleado
    from collections import defaultdict
    emp_data: dict = defaultdict(lambda: {
        "por_modelo": defaultdict(int),   # modelo_id → minutos
        "sin_plan": 0,                     # horas sin plan de turno
        "total": 0,
        "dias": [],
    })

    for f in fichajes:
        emp_id_str = str(f.empleado_id)
        fecha_iso = f.fecha_entrada.date().isoformat()
        duracion = f.minutos_trabajados or 0

        # Buscar plan de turno para ese día
        plan = plan_idx.get((emp_id_str, fecha_iso))

        emp_data[emp_id_str]["total"] += duracion
        emp_data[emp_id_str]["dias"].append({
            "fecha": fecha_iso,
            "entrada": f.fecha_entrada.strftime("%H:%M"),
            "salida": f.fecha_salida.strftime("%H:%M") if f.fecha_salida else None,
            "minutos": duracion,
            "modelo_codigo": plan.modelo_turno.codigo if plan and plan.modelo_turno else None,
            "modelo_nombre": plan.modelo_turno.nombre if plan and plan.modelo_turno else "Ohne Plan",
            "modelo_color": plan.modelo_turno.color if plan and plan.modelo_turno else "#9ca3af",
        })

        if plan and plan.modelo_turno_id:
            emp_data[emp_id_str]["por_modelo"][str(plan.modelo_turno_id)] += duracion
        else:
            emp_data[emp_id_str]["sin_plan"] += duracion

    # Construir respuesta
    empleados_out = []
    totales: dict = defaultdict(int)
    totales["sin_plan"] = 0
    totales["total"] = 0

    for emp_id_str, data in sorted(
        emp_data.items(),
        key=lambda x: emp_map[x[0]].id_nummer if x[0] in emp_map else 0
    ):
        emp = emp_map.get(emp_id_str)
        por_modelo_out = {}
        for m in modelos:
            mins = data["por_modelo"].get(str(m.id), 0)
            por_modelo_out[m.codigo] = {"minutos": mins, "formateado": _fmt(mins), "modelo_id": str(m.id)}
            totales[m.codigo] = totales.get(m.codigo, 0) + mins

        totales["sin_plan"] += data["sin_plan"]
        totales["total"] += data["total"]

        empleados_out.append({
            "empleado_id": emp_id_str,
            "id_nummer": emp.id_nummer if emp else None,
            "nombre": f"{emp.nombre} {emp.apellido or ''}".strip() if emp else emp_id_str,
            "por_modelo": por_modelo_out,
            "sin_plan_minutos": data["sin_plan"],
            "sin_plan_formateado": _fmt(data["sin_plan"]),
            "total_minutos": data["total"],
            "total_formateado": _fmt(data["total"]),
            "detalle_diario": sorted(data["dias"], key=lambda d: d["fecha"]),
        })

    # Formatear totales
    totales_out = {k: {"minutos": v, "formateado": _fmt(v)} for k, v in totales.items()}

    return {
        "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "modelos": [
            {"id": str(m.id), "codigo": m.codigo, "nombre": m.nombre, "color": m.color or "#6b7280"}
            for m in modelos
        ],
        "empleados": empleados_out,
        "totales": totales_out,
    }
