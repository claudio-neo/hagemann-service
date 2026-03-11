"""
API de Saldo de Horas Mensual — Hagemann
Stundenkonto / Arbeitszeitkonto con Stundenkappung
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from decimal import Decimal
from typing import Optional
from uuid import UUID
from datetime import date

from ..database import get_db
from ..models.empleado import Empleado
from ..models.saldo_horas import SaldoHorasMensual
from ..services.calculo_saldo import (
    calcular_saldo_mes,
    calcular_saldo_anio,
    cierre_mensual_todos,
    _saldo_to_dict,
    DEFAULT_LIMITE_KAPPUNG,
)

router = APIRouter(prefix="/saldo-horas", tags=["Saldo de Horas"])


# ========== ENDPOINTS ==========

@router.get("/{empleado_id}")
def saldo_horas_empleado(
    empleado_id: UUID,
    year: int = Query(..., description="Año (ej: 2026)"),
    kappung: Optional[float] = Query(
        None,
        description=f"Límite Stundenkappung en horas (default: {float(DEFAULT_LIMITE_KAPPUNG)})"
    ),
    db: Session = Depends(get_db),
):
    """
    Calcula el saldo de horas mensual de un empleado para un año completo.

    Por cada mes devuelve:
    - **horas_planificadas**: horas según contrato (monthly_hours)
    - **horas_reales**: suma de fichajes cerrados
    - **saldo_mes**: real - planificado
    - **carryover_anterior**: saldo final del mes anterior
    - **saldo_acumulado**: saldo_mes + carryover
    - **saldo_final**: después de aplicar Stundenkappung
    - **kappung_aplicada**: si se recortó el saldo
    """
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Empleado no encontrado")

    limite = Decimal(str(kappung)) if kappung is not None else None
    meses = calcular_saldo_anio(db, empleado_id, year, limite_kappung=limite)

    # Resumen del año
    total_planificado = sum(m["horas_planificadas"] for m in meses)
    total_real = sum(m["horas_reales"] for m in meses)
    saldo_total = total_real - total_planificado
    saldo_final_anio = meses[-1]["saldo_final"] if meses else 0.0

    return {
        "empleado": {
            "id": str(emp.id),
            "nombre": f"{emp.nombre} {emp.apellido or ''}".strip(),
            "monthly_hours": emp.monthly_hours,
        },
        "anio": year,
        "limite_kappung": kappung or float(DEFAULT_LIMITE_KAPPUNG),
        "resumen": {
            "total_planificado": round(total_planificado, 2),
            "total_real": round(total_real, 2),
            "saldo_total": round(saldo_total, 2),
            "saldo_acumulado_diciembre": round(saldo_final_anio, 2),
        },
        "meses": meses,
    }


@router.get("/{empleado_id}/mes/{mes}")
def saldo_mes_empleado(
    empleado_id: UUID,
    mes: int,
    year: int = Query(..., description="Año (ej: 2026)"),
    kappung: Optional[float] = Query(None),
    forzar: bool = Query(False, description="Forzar recálculo aunque esté cerrado"),
    db: Session = Depends(get_db),
):
    """Saldo de un mes concreto para un empleado."""
    if not 1 <= mes <= 12:
        raise HTTPException(400, "El mes debe estar entre 1 y 12")

    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Empleado no encontrado")

    limite = Decimal(str(kappung)) if kappung is not None else None
    resultado = calcular_saldo_mes(
        db, empleado_id, year, mes,
        limite_kappung=limite,
        forzar_recalculo=forzar,
    )
    return resultado


@router.post("/{empleado_id}/cerrar-mes")
def cerrar_mes_empleado(
    empleado_id: UUID,
    year: int = Query(...),
    mes: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
):
    """
    Marca el saldo de un mes como CERRADO.
    Un saldo cerrado no se recalcula automáticamente (requiere forzar=True).
    """
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Empleado no encontrado")

    saldo = db.query(SaldoHorasMensual).filter(
        SaldoHorasMensual.empleado_id == empleado_id,
        SaldoHorasMensual.anio == year,
        SaldoHorasMensual.mes == mes,
    ).first()

    if not saldo:
        # Calcular y cerrar en un paso
        resultado = calcular_saldo_mes(db, empleado_id, year, mes)
        saldo = db.query(SaldoHorasMensual).filter(
            SaldoHorasMensual.empleado_id == empleado_id,
            SaldoHorasMensual.anio == year,
            SaldoHorasMensual.mes == mes,
        ).first()

    if saldo:
        saldo.cerrado = True
        db.commit()

    return {"message": f"Saldo {year}/{mes:02d} cerrado para {emp.nombre}", "id": str(saldo.id) if saldo else None}


@router.get("/cierre-mensual/calcular")
def cierre_mensual(
    year: int = Query(..., description="Año (ej: 2026)"),
    mes: int = Query(..., ge=1, le=12, description="Mes (1-12)"),
    kappung: Optional[float] = Query(None),
    solo_activos: bool = Query(True),
    db: Session = Depends(get_db),
):
    """
    Genera/recalcula saldos de TODOS los empleados para un mes dado.
    Útil para el cierre de mes de RRHH.

    Devuelve resumen con todos los empleados procesados.
    """
    limite = Decimal(str(kappung)) if kappung is not None else None
    resultado = cierre_mensual_todos(
        db, year, mes,
        limite_kappung=limite,
        solo_activos=solo_activos,
    )
    return resultado


@router.get("/historial/{empleado_id}")
def historial_saldo(
    empleado_id: UUID,
    limit: int = Query(24, ge=1, le=60, description="Últimos N meses"),
    db: Session = Depends(get_db),
):
    """Historial de saldos guardados para un empleado (los últimos N meses)."""
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Empleado no encontrado")

    saldos = (
        db.query(SaldoHorasMensual)
        .filter(SaldoHorasMensual.empleado_id == empleado_id)
        .order_by(
            SaldoHorasMensual.anio.desc(),
            SaldoHorasMensual.mes.desc(),
        )
        .limit(limit)
        .all()
    )

    return {
        "empleado": {
            "id": str(emp.id),
            "nombre": f"{emp.nombre} {emp.apellido or ''}".strip(),
        },
        "data": [_saldo_to_dict(s, emp) for s in saldos],
        "total": len(saldos),
    }
