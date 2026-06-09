"""
API de Reportes — doble vista empleado↔departamento + horas por tipo de turno
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import Optional
from uuid import UUID
from datetime import date, datetime

from ..database import get_db
from ..models.empleado import Empleado, CentroCoste
from ..models.fichaje import Fichaje, SegmentoTiempo
from ..models.turno import PlanTurno, ModeloTurno
from ..auth import require_permission, get_current_user
from ..permisos import REPORTS_VIEW, scoped_empleado_ids, assert_empleado_accesible

router = APIRouter(
    prefix="/reportes",
    tags=["Reportes"],
    dependencies=[Depends(require_permission(REPORTS_VIEW))],
)


def _seg_minutos(seg_inicio: datetime, seg_fin: Optional[datetime], seg_minutos: Optional[int],
                 hasta_dt: datetime, now_dt: datetime) -> int:
    """Minutos efectivos del segmento. Si está abierto, cuenta hasta min(now, hasta_dt)."""
    if seg_fin is not None:
        return int(seg_minutos or 0)
    # Segmento abierto — contar hasta el corte del período
    cutoff = min(now_dt, hasta_dt)
    if cutoff <= seg_inicio:
        return 0
    return int((cutoff - seg_inicio).total_seconds() / 60)


def _periodo_filter(desde_dt: datetime, hasta_dt: datetime):
    """Filtro: segmentos cerrados dentro del rango O abiertos que empezaron en el rango."""
    return and_(
        SegmentoTiempo.inicio >= desde_dt,
        SegmentoTiempo.inicio <= hasta_dt,
        or_(
            SegmentoTiempo.fin.is_(None),                 # abiertos
            SegmentoTiempo.fin <= hasta_dt,               # cerrados dentro del rango
        ),
    )


@router.get("/horas-empleado")
def horas_por_empleado(
    empleado_id: UUID,
    desde: date = Query(...),
    hasta: date = Query(...),
    db: Session = Depends(get_db),
    _auth=Depends(get_current_user),
):
    """
    Horas de un empleado desglosadas por centro de coste.
    Vista: ¿Cuántas horas trabajó René y en qué departamentos?
    """
    assert_empleado_accesible(_auth, db, empleado_id)
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Mitarbeiter nicht gefunden")

    desde_dt = datetime.combine(desde, datetime.min.time())
    hasta_dt = datetime.combine(hasta, datetime.max.time())
    now_dt = datetime.utcnow()

    # Cargar segmentos cerrados y abiertos del período (incluye jornadas en curso)
    segmentos = (
        db.query(
            func.date(SegmentoTiempo.inicio).label("dia"),
            CentroCoste.id.label("cc_id"),
            CentroCoste.codigo.label("cc_codigo"),
            CentroCoste.nombre.label("cc_nombre"),
            SegmentoTiempo.inicio,
            SegmentoTiempo.fin,
            SegmentoTiempo.minutos,
        )
        .join(CentroCoste, SegmentoTiempo.centro_coste_id == CentroCoste.id)
        .filter(
            SegmentoTiempo.empleado_id == empleado_id,
            _periodo_filter(desde_dt, hasta_dt),
        )
        .order_by(SegmentoTiempo.inicio)
        .all()
    )

    # Agregar por centro de coste y por día
    by_cc_map = {}
    daily_grouped = {}
    for s in segmentos:
        m = _seg_minutos(s.inicio, s.fin, s.minutos, hasta_dt, now_dt)
        cc_key = str(s.cc_id)
        if cc_key not in by_cc_map:
            by_cc_map[cc_key] = {"id": cc_key, "codigo": s.cc_codigo, "nombre": s.cc_nombre, "minutos": 0}
        by_cc_map[cc_key]["minutos"] += m

        day_key = str(s.dia)
        if day_key not in daily_grouped:
            daily_grouped[day_key] = {"fecha": day_key, "total_minutos": 0, "segmentos": []}
        daily_grouped[day_key]["segmentos"].append({
            "centro_coste": s.cc_nombre,
            "inicio": s.inicio.strftime("%H:%M"),
            "fin": s.fin.strftime("%H:%M") if s.fin else None,
            "en_curso": s.fin is None,
            "minutos": m,
        })
        daily_grouped[day_key]["total_minutos"] += m

    by_cc = list(by_cc_map.values())
    total_minutes = sum(r["minutos"] for r in by_cc)

    # Raucherpausen del período, agrupadas por día (informativo; ya descontadas del neto)
    from ..models.pausa import Pausa
    pausas = (
        db.query(Pausa.inicio, Pausa.minutos)
        .filter(
            Pausa.empleado_id == empleado_id,
            Pausa.inicio >= desde_dt,
            Pausa.inicio <= hasta_dt,
            Pausa.tipo == "RAUCH",
        )
        .all()
    )
    rauch_by_day = {}
    total_rauch_min = 0
    total_rauch_cnt = 0
    for p in pausas:
        day_key = str(p.inicio.date())
        m = int(p.minutos or 0)
        d = rauch_by_day.setdefault(day_key, {"minutos": 0, "count": 0})
        d["minutos"] += m
        d["count"] += 1
        total_rauch_min += m
        total_rauch_cnt += 1

    # Días con Raucherpause pero sin segmentos de trabajo → crear entrada
    for day_key in rauch_by_day:
        if day_key not in daily_grouped:
            daily_grouped[day_key] = {"fecha": day_key, "total_minutos": 0, "segmentos": []}

    detalle = sorted(daily_grouped.values(), key=lambda x: x["fecha"])
    for d in detalle:
        r = rauch_by_day.get(d["fecha"], {"minutos": 0, "count": 0})
        d["raucherpause_minutos"] = r["minutos"]
        d["raucherpause_count"] = r["count"]

    return {
        "empleado": {
            "id": str(emp.id),
            "id_nummer": emp.id_nummer,
            "nombre": f"{emp.nombre} {emp.apellido or ''}".strip(),
        },
        "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "total_minutos": total_minutes,
        "total_formateado": f"{total_minutes // 60}:{total_minutes % 60:02d}",
        "total_raucherpause_minutos": total_rauch_min,
        "total_raucherpause_count": total_rauch_cnt,
        "por_centro_coste": [
            {
                "centro_coste_id": r["id"],
                "codigo": r["codigo"],
                "nombre": r["nombre"],
                "minutos": r["minutos"],
                "formateado": f"{r['minutos'] // 60}:{r['minutos'] % 60:02d}",
                "porcentaje": round(r["minutos"] / total_minutes * 100, 1) if total_minutes else 0,
            }
            for r in by_cc
        ],
        "detalle_diario": detalle,
    }


@router.get("/horas-centro-coste")
def horas_por_centro_coste(
    centro_coste_id: UUID,
    desde: date = Query(...),
    hasta: date = Query(...),
    db: Session = Depends(get_db),
    _auth=Depends(get_current_user),
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
    now_dt = datetime.utcnow()

    scope_ids = scoped_empleado_ids(_auth, db)
    seg_query = (
        db.query(
            Empleado.id.label("emp_id"),
            Empleado.id_nummer,
            Empleado.nombre,
            Empleado.apellido,
            SegmentoTiempo.inicio,
            SegmentoTiempo.fin,
            SegmentoTiempo.minutos,
        )
        .join(SegmentoTiempo, SegmentoTiempo.empleado_id == Empleado.id)
        .filter(
            SegmentoTiempo.centro_coste_id == centro_coste_id,
            _periodo_filter(desde_dt, hasta_dt),
        )
    )
    if scope_ids is not None:
        seg_query = seg_query.filter(SegmentoTiempo.empleado_id.in_(scope_ids))
    segmentos = seg_query.all()

    by_emp_map = {}
    for s in segmentos:
        m = _seg_minutos(s.inicio, s.fin, s.minutos, hasta_dt, now_dt)
        key = str(s.emp_id)
        if key not in by_emp_map:
            by_emp_map[key] = {
                "id": key, "id_nummer": s.id_nummer,
                "nombre": f"{s.nombre} {s.apellido or ''}".strip(),
                "minutos": 0,
            }
        by_emp_map[key]["minutos"] += m
    by_emp = sorted(by_emp_map.values(), key=lambda x: -x["minutos"])
    total_minutes = sum(r["minutos"] for r in by_emp)

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
                "empleado_id": r["id"],
                "id_nummer": r["id_nummer"],
                "nombre": r["nombre"],
                "minutos": r["minutos"],
                "formateado": f"{r['minutos'] // 60}:{r['minutos'] % 60:02d}",
                "porcentaje": round(r["minutos"] / total_minutes * 100, 1) if total_minutes else 0,
            }
            for r in by_emp
        ],
    }


@router.get("/resumen-centros-coste")
def resumen_centros_coste(
    desde: date = Query(...),
    hasta: date = Query(...),
    db: Session = Depends(get_db),
    _auth=Depends(get_current_user),
):
    """
    Vista gerencial: todos los centros de coste con total de horas y empleados.
    """
    desde_dt = datetime.combine(desde, datetime.min.time())
    hasta_dt = datetime.combine(hasta, datetime.max.time())
    now_dt = datetime.utcnow()

    # Cargar todos los segmentos del período (cerrados + abiertos)
    scope_ids = scoped_empleado_ids(_auth, db)
    seg_query = (
        db.query(
            SegmentoTiempo.centro_coste_id,
            SegmentoTiempo.empleado_id,
            SegmentoTiempo.inicio,
            SegmentoTiempo.fin,
            SegmentoTiempo.minutos,
        )
        .filter(_periodo_filter(desde_dt, hasta_dt))
    )
    if scope_ids is not None:
        seg_query = seg_query.filter(SegmentoTiempo.empleado_id.in_(scope_ids))
    segmentos = seg_query.all()

    # Agregar por CC
    cc_agg = {}  # cc_id -> {"min": int, "emp_ids": set()}
    for s in segmentos:
        m = _seg_minutos(s.inicio, s.fin, s.minutos, hasta_dt, now_dt)
        key = str(s.centro_coste_id)
        if key not in cc_agg:
            cc_agg[key] = {"min": 0, "emp_ids": set()}
        cc_agg[key]["min"] += m
        cc_agg[key]["emp_ids"].add(s.empleado_id)

    # Listar todos los CCs activos para incluir los que tienen 0 minutos
    centros = (
        db.query(CentroCoste.id, CentroCoste.codigo, CentroCoste.nombre, CentroCoste.color)
        .filter(CentroCoste.activo == True)
        .order_by(CentroCoste.codigo)
        .all()
    )
    results = []
    for c in centros:
        agg = cc_agg.get(str(c.id), {"min": 0, "emp_ids": set()})
        results.append(type("Row", (), {
            "id": c.id, "codigo": c.codigo, "nombre": c.nombre, "color": c.color,
            "total_min": agg["min"], "emp_count": len(agg["emp_ids"]),
        }))

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
    _auth=Depends(get_current_user),
):
    """
    HG-22: Horas reales trabajadas por empleado desglosadas por tipo de turno.
    Cruza Fichaje (horas reales) con PlanTurno (modelo asignado ese día).

    Útil para calcular Nachtzuschläge, Frühschicht-Zulagen, etc.
    """
    if empleado_id:
        assert_empleado_accesible(_auth, db, empleado_id)
    scope_ids = scoped_empleado_ids(_auth, db)
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
    elif scope_ids is not None:
        q = q.filter(Fichaje.empleado_id.in_(scope_ids))
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
