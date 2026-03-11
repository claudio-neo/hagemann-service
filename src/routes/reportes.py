"""
API de Reportes — doble vista empleado↔departamento
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
        raise HTTPException(404, "Empleado no encontrado")

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
        raise HTTPException(404, "Centro de coste no encontrado")

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
