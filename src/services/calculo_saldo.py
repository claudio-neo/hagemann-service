"""
Servicio de cálculo de saldo de horas mensual — Hagemann
Lógica de negocio separada de las rutas para testabilidad.
"""
from decimal import Decimal
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, extract

from ..models.empleado import Empleado
from ..models.fichaje import Fichaje
from ..models.saldo_horas import SaldoHorasMensual

# Stundenkappung por defecto: 100 horas extra acumuladas como máximo
DEFAULT_LIMITE_KAPPUNG = Decimal("100.00")


def _get_horas_reales_mes(db: Session, empleado_id, anio: int, mes: int) -> Decimal:
    """
    Suma los minutos trabajados en fichajes CERRADOS del mes y los convierte a horas.
    Sólo cuenta fichajes con fecha_salida (jornadas completas).
    """
    result = db.query(
        func.coalesce(func.sum(Fichaje.minutos_trabajados), 0)
    ).filter(
        Fichaje.empleado_id == empleado_id,
        Fichaje.fecha_salida.isnot(None),
        extract("year", Fichaje.fecha_entrada) == anio,
        extract("month", Fichaje.fecha_entrada) == mes,
    ).scalar()

    return (Decimal(str(result)) / Decimal("60")).quantize(Decimal("0.01"))


def _get_carryover_anterior(db: Session, empleado_id, anio: int, mes: int) -> Decimal:
    """
    Obtiene el saldo_final del mes anterior.
    Si no existe registro (primer mes), devuelve 0.
    """
    if mes == 1:
        prev_anio, prev_mes = anio - 1, 12
    else:
        prev_anio, prev_mes = anio, mes - 1

    registro_anterior = db.query(SaldoHorasMensual).filter(
        SaldoHorasMensual.empleado_id == empleado_id,
        SaldoHorasMensual.anio == prev_anio,
        SaldoHorasMensual.mes == prev_mes,
    ).first()

    if registro_anterior:
        return Decimal(str(registro_anterior.saldo_final))
    return Decimal("0.00")


def calcular_saldo_mes(
    db: Session,
    empleado_id,
    anio: int,
    mes: int,
    limite_kappung: Optional[Decimal] = None,
    forzar_recalculo: bool = False,
) -> Dict[str, Any]:
    """
    Calcula el saldo de horas de un empleado para un mes.

    Parámetros:
      empleado_id: UUID del empleado
      anio, mes: período
      limite_kappung: horas extra máximas acumuladas (None = usar default 100h)
      forzar_recalculo: True = recalcula aunque esté cerrado

    Devuelve dict con todos los campos del saldo + mensaje.
    """
    emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    if not emp:
        raise ValueError(f"Empleado {empleado_id} no encontrado")

    # Si ya existe y está cerrado y no forzamos → devolver el existente
    existente = db.query(SaldoHorasMensual).filter(
        SaldoHorasMensual.empleado_id == empleado_id,
        SaldoHorasMensual.anio == anio,
        SaldoHorasMensual.mes == mes,
    ).first()

    if existente and existente.cerrado and not forzar_recalculo:
        return _saldo_to_dict(existente, emp)

    # Calcular valores
    kappung = limite_kappung if limite_kappung is not None else DEFAULT_LIMITE_KAPPUNG

    horas_planificadas = Decimal(str(emp.monthly_hours))
    horas_reales = _get_horas_reales_mes(db, empleado_id, anio, mes)
    saldo_mes = (horas_reales - horas_planificadas).quantize(Decimal("0.01"))
    carryover_anterior = _get_carryover_anterior(db, empleado_id, anio, mes)
    saldo_acumulado = (saldo_mes + carryover_anterior).quantize(Decimal("0.01"))

    # Stundenkappung: limitar saldo acumulado positivo
    kappung_aplicada = False
    horas_cortadas = Decimal("0.00")
    saldo_final = saldo_acumulado

    if saldo_acumulado > kappung:
        horas_cortadas = (saldo_acumulado - kappung).quantize(Decimal("0.01"))
        saldo_final = kappung
        kappung_aplicada = True
    elif saldo_acumulado < -kappung:
        # También capamos hacia abajo (deuda máxima)
        horas_cortadas = (-kappung - saldo_acumulado).quantize(Decimal("0.01"))
        saldo_final = -kappung
        kappung_aplicada = True

    now = datetime.utcnow()

    if existente:
        existente.horas_planificadas = horas_planificadas
        existente.horas_reales = horas_reales
        existente.saldo_mes = saldo_mes
        existente.carryover_anterior = carryover_anterior
        existente.saldo_acumulado = saldo_acumulado
        existente.limite_kappung = kappung
        existente.saldo_final = saldo_final
        existente.kappung_aplicada = kappung_aplicada
        existente.horas_cortadas = horas_cortadas
        existente.calculado_en = now
        db.commit()
        db.refresh(existente)
        return _saldo_to_dict(existente, emp)
    else:
        saldo = SaldoHorasMensual(
            empleado_id=empleado_id,
            anio=anio,
            mes=mes,
            horas_planificadas=horas_planificadas,
            horas_reales=horas_reales,
            saldo_mes=saldo_mes,
            carryover_anterior=carryover_anterior,
            saldo_acumulado=saldo_acumulado,
            limite_kappung=kappung,
            saldo_final=saldo_final,
            kappung_aplicada=kappung_aplicada,
            horas_cortadas=horas_cortadas,
            cerrado=False,
            calculado_en=now,
        )
        db.add(saldo)
        db.commit()
        db.refresh(saldo)
        return _saldo_to_dict(saldo, emp)


def calcular_saldo_anio(
    db: Session,
    empleado_id,
    anio: int,
    limite_kappung: Optional[Decimal] = None,
) -> List[Dict[str, Any]]:
    """
    Calcula el saldo mensual de los 12 meses de un año.
    Respeta carryover encadenado mes a mes.
    """
    resultados = []
    for mes in range(1, 13):
        resultado = calcular_saldo_mes(
            db, empleado_id, anio, mes,
            limite_kappung=limite_kappung,
        )
        resultados.append(resultado)
    return resultados


def cierre_mensual_todos(
    db: Session,
    anio: int,
    mes: int,
    limite_kappung: Optional[Decimal] = None,
    solo_activos: bool = True,
) -> Dict[str, Any]:
    """
    Genera/recalcula saldos de todos los empleados para un mes.
    Útil para el cierre de mes.
    """
    query = db.query(Empleado)
    if solo_activos:
        query = query.filter(Empleado.activo == True)
    empleados = query.all()

    resultados = []
    errores = []

    for emp in empleados:
        try:
            res = calcular_saldo_mes(
                db, emp.id, anio, mes,
                limite_kappung=limite_kappung,
                forzar_recalculo=True,
            )
            resultados.append(res)
        except Exception as e:
            errores.append({
                "empleado_id": str(emp.id),
                "nombre": f"{emp.nombre} {emp.apellido or ''}".strip(),
                "error": str(e),
            })

    return {
        "anio": anio,
        "mes": mes,
        "procesados": len(resultados),
        "errores": len(errores),
        "resultados": resultados,
        "errores_detalle": errores,
    }


def _saldo_to_dict(s: SaldoHorasMensual, emp: Optional[Empleado] = None) -> Dict[str, Any]:
    return {
        "id": str(s.id),
        "empleado_id": str(s.empleado_id),
        "empleado_nombre": (
            f"{emp.nombre} {emp.apellido or ''}".strip() if emp else None
        ),
        "anio": s.anio,
        "mes": s.mes,
        "mes_nombre": _mes_nombre(s.mes),
        "horas_planificadas": float(s.horas_planificadas),
        "horas_reales": float(s.horas_reales),
        "saldo_mes": float(s.saldo_mes),
        "saldo_mes_label": _format_horas(float(s.saldo_mes)),
        "carryover_anterior": float(s.carryover_anterior),
        "saldo_acumulado": float(s.saldo_acumulado),
        "limite_kappung": float(s.limite_kappung) if s.limite_kappung else None,
        "saldo_final": float(s.saldo_final),
        "saldo_final_label": _format_horas(float(s.saldo_final)),
        "kappung_aplicada": s.kappung_aplicada,
        "horas_cortadas": float(s.horas_cortadas),
        "cerrado": s.cerrado,
        "calculado_en": s.calculado_en.isoformat() if s.calculado_en else None,
    }


def _mes_nombre(mes: int) -> str:
    nombres = [
        "", "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember"
    ]
    return nombres[mes] if 1 <= mes <= 12 else str(mes)


def _format_horas(horas: float) -> str:
    """Formatea horas como ±XXh YYmin"""
    signo = "+" if horas >= 0 else "-"
    abs_h = abs(horas)
    h = int(abs_h)
    m = int((abs_h - h) * 60)
    return f"{signo}{h}h {m:02d}min"
