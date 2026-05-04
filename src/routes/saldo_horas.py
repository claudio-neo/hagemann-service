"""
API de Saldo de Horas Mensual — Hagemann
Stundenkonto / Arbeitszeitkonto con Stundenkappung
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from decimal import Decimal
from typing import Optional
from uuid import UUID
from datetime import date, datetime

from pydantic import BaseModel

from ..database import get_db
from ..models.empleado import Empleado
from ..models.saldo_horas import SaldoHorasMensual
from ..auth import require_permission
from ..permisos import TIMECLOCK_VIEW_OWN, HOURS_CONTROL_TEAM, HOURS_RELEASE_TEAM
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
    _auth=Depends(require_permission(TIMECLOCK_VIEW_OWN)),
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
        raise HTTPException(404, "Mitarbeiter nicht gefunden")

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
    _auth=Depends(require_permission(TIMECLOCK_VIEW_OWN)),
):
    """Saldo de un mes concreto para un empleado."""
    if not 1 <= mes <= 12:
        raise HTTPException(400, "Der Monat muss zwischen 1 und 12 liegen")

    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Mitarbeiter nicht gefunden")

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
    _auth=Depends(require_permission(HOURS_RELEASE_TEAM)),
):
    """
    Marca el saldo de un mes como CERRADO.
    Un saldo cerrado no se recalcula automáticamente (requiere forzar=True).
    """
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Mitarbeiter nicht gefunden")

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
    _auth=Depends(require_permission(HOURS_CONTROL_TEAM)),
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
    _auth=Depends(require_permission(TIMECLOCK_VIEW_OWN)),
):
    """Historial de saldos guardados para un empleado (los últimos N meses)."""
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Mitarbeiter nicht gefunden")

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


class AjusteManualBody(BaseModel):
    horas_reales: float
    horas_planificadas: Optional[float] = None   # None → usa monthly_hours del empleado
    notas: Optional[str] = None


@router.post("/{empleado_id}/mes/{mes}/ajuste")
def ajuste_manual_mes(
    empleado_id: UUID,
    mes: int,
    year: int = Query(..., ge=2020, le=2099),
    body: AjusteManualBody = ...,
    db: Session = Depends(get_db),
    _auth=Depends(require_permission(HOURS_CONTROL_TEAM)),
):
    """
    Ajuste manual de un mes: permite al admin corregir horas reales y/o planificadas
    y marca el mes como CERRADO para que no se recalcule automáticamente.
    Útil para establecer el saldo de meses anteriores al inicio del sistema.
    """
    if not 1 <= mes <= 12:
        raise HTTPException(400, "Mes entre 1 y 12")

    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise HTTPException(404, "Mitarbeiter nicht gefunden")

    from ..services.calculo_saldo import _get_carryover_anterior, DEFAULT_LIMITE_KAPPUNG

    horas_planificadas = Decimal(str(body.horas_planificadas)) if body.horas_planificadas is not None else Decimal(str(emp.monthly_hours))
    horas_reales = Decimal(str(body.horas_reales))
    saldo_mes = (horas_reales - horas_planificadas).quantize(Decimal("0.01"))
    carryover = _get_carryover_anterior(db, empleado_id, year, mes)
    saldo_acumulado = (saldo_mes + carryover).quantize(Decimal("0.01"))
    kappung = DEFAULT_LIMITE_KAPPUNG
    saldo_final = max(min(saldo_acumulado, kappung), -kappung)
    kappung_aplicada = saldo_final != saldo_acumulado

    existente = db.query(SaldoHorasMensual).filter(
        SaldoHorasMensual.empleado_id == empleado_id,
        SaldoHorasMensual.anio == year,
        SaldoHorasMensual.mes == mes,
    ).first()

    if existente:
        existente.horas_planificadas = horas_planificadas
        existente.horas_reales = horas_reales
        existente.saldo_mes = saldo_mes
        existente.carryover_anterior = carryover
        existente.saldo_acumulado = saldo_acumulado
        existente.saldo_final = saldo_final
        existente.kappung_aplicada = kappung_aplicada
        existente.horas_cortadas = abs(saldo_acumulado - saldo_final)
        existente.cerrado = True
        existente.notas = body.notas
        existente.calculado_en = datetime.utcnow()
    else:
        existente = SaldoHorasMensual(
            empleado_id=empleado_id,
            anio=year,
            mes=mes,
            horas_planificadas=horas_planificadas,
            horas_reales=horas_reales,
            saldo_mes=saldo_mes,
            carryover_anterior=carryover,
            saldo_acumulado=saldo_acumulado,
            limite_kappung=kappung,
            saldo_final=saldo_final,
            kappung_aplicada=kappung_aplicada,
            horas_cortadas=abs(saldo_acumulado - saldo_final),
            cerrado=True,
            notas=body.notas,
            calculado_en=datetime.utcnow(),
        )
        db.add(existente)

    db.commit()
    db.refresh(existente)
    return {
        "ok": True,
        "mes": f"{year}/{mes:02d}",
        "empleado": emp.nombre,
        "saldo_final": float(existente.saldo_final),
        "notas": existente.notas,
    }
