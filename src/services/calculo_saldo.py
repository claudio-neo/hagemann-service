"""
Servicio de cálculo de saldo de horas mensual — Hagemann
Lógica de negocio separada de las rutas para testabilidad.
"""
from decimal import Decimal
from typing import Optional, List, Dict, Any, Set
from datetime import datetime, date, timedelta
from calendar import monthrange
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, extract

from ..models.empleado import Empleado
from ..models.fichaje import Fichaje
from ..models.saldo_horas import SaldoHorasMensual
from ..models.vacaciones import (
    SolicitudVacaciones, EstadoSolicitud, Festivo,
    SaldoWirkung, saldo_wirkung,
)


def _worked_minutes_day(db: Session, empleado_id, d: date) -> Decimal:
    """Minutos trabajados (fichajes cerrados) en un día concreto, en horas."""
    result = db.query(
        func.coalesce(func.sum(Fichaje.minutos_trabajados), 0)
    ).filter(
        Fichaje.empleado_id == empleado_id,
        Fichaje.fecha_salida.isnot(None),
        func.date(Fichaje.fecha_entrada) == d,
    ).scalar()
    return (Decimal(str(result)) / Decimal("60")).quantize(Decimal("0.01"))

# ⚠️ Kappung DESACTIVADA por requisito de la Personalabteilung (2026-06):
# "weder Plus- noch Minusstunden pauschal gekappt oder zurückgesetzt".
# Se mantiene la constante por compatibilidad de imports, pero NO se aplica.
DEFAULT_LIMITE_KAPPUNG = Decimal("100.00")

# Días con media jornada obligatoria libre (halbe Sollzeit): 24.12 y 31.12.
_HALBE_TAGE = {(12, 24), (12, 31)}


def _factor_dia(d: date) -> Decimal:
    """Factor de Sollzeit del día: 0.5 en 24.12 y 31.12 (medio día libre), 1.0 resto."""
    return Decimal("0.5") if (d.month, d.day) in _HALBE_TAGE else Decimal("1")


def _festivos_mes(db: Session, anio: int, mes: int) -> Set[date]:
    """Conjunto de fechas festivas del mes (nacionales DE + Sachsen)."""
    dias_mes = monthrange(anio, mes)[1]
    rows = db.query(Festivo.fecha).filter(
        Festivo.activo == True,
        Festivo.bundesland.in_(["DE", "SN"]),
        Festivo.fecha >= date(anio, mes, 1),
        Festivo.fecha <= date(anio, mes, dias_mes),
    ).all()
    return {r[0] for r in rows}


def _es_laborable(d: date, festivos: Set[date]) -> bool:
    """Mo–Fr y no festivo."""
    return d.weekday() < 5 and d not in festivos


def _sollzeit_ponderada(desde: date, hasta: date, festivos: Set[date]) -> Decimal:
    """Suma de factores de día laborable en [desde, hasta] (24/31-Dic cuentan 0.5)."""
    total = Decimal("0")
    d = desde
    while d <= hasta:
        if _es_laborable(d, festivos):
            total += _factor_dia(d)
        d += timedelta(days=1)
    return total


def _credito_ausencias_mes(
    db: Session,
    empleado_id,
    rango_desde: date,
    rango_hasta: date,
    festivos: Set[date],
    tagessollzeit: Decimal,
) -> Decimal:
    """
    Horas acreditadas al saldo por ausencias aprobadas del tipo AUFFUELLEN
    (auf Sollzeit auffüllen), restringidas al rango efectivo [rango_desde,
    rango_hasta] (mismo que las horas planificadas: respeta alta/baja/mes en curso).

    Tres comportamientos:
      - AUFFUELLEN: día completo → acredita tagessollzeit·factor_dia (·0.5 si medio día).
      - AUFFUELLEN_REST (Arzt-Gang): rellena hasta la Sollzeit del día contando lo
        ya trabajado ese día → acredita max(0, Soll_dia − horas_trabajadas_dia).
      - UNTERBRECHUNG (FZA, falta injustificada): no acredita nada.
    """
    if rango_hasta < rango_desde:
        return Decimal("0.00")

    solicitudes = db.query(SolicitudVacaciones).filter(
        SolicitudVacaciones.empleado_id == empleado_id,
        SolicitudVacaciones.estado == EstadoSolicitud.APROBADA,
        SolicitudVacaciones.fecha_inicio <= rango_hasta,
        SolicitudVacaciones.fecha_fin >= rango_desde,
    ).all()

    credito = Decimal("0")
    for s in solicitudes:
        wirkung = saldo_wirkung(s.tipo_ausencia)
        if wirkung == SaldoWirkung.UNTERBRECHUNG:
            continue
        factor_medio = Decimal("0.5") if getattr(s, "medio_dia", False) else Decimal("1")
        desde = max(s.fecha_inicio, rango_desde)
        hasta = min(s.fecha_fin, rango_hasta)
        d = desde
        while d <= hasta:
            if _es_laborable(d, festivos):
                soll_dia = tagessollzeit * _factor_dia(d) * factor_medio
                if wirkung == SaldoWirkung.AUFFUELLEN_REST:
                    # Top-up: completa hasta la Sollzeit lo que no se trabajó ese día
                    trabajado = _worked_minutes_day(db, empleado_id, d)
                    credito += max(Decimal("0"), soll_dia - trabajado)
                else:  # AUFFUELLEN — día completo
                    credito += soll_dia
            d += timedelta(days=1)
    return credito.quantize(Decimal("0.01"))


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

    # ── Determinar si el mes está fuera del rango de cálculo del empleado ──
    # Antes de beginn_berechnung (o fecha_alta), o después de fecha_baja: 0/0/0
    # También: meses futuros nunca generan saldo negativo
    hoy = date.today()
    mes_fin = date(anio, mes, 28)  # representa "fin de mes" en sentido amplio
    mes_inicio = date(anio, mes, 1)
    inicio_calculo = emp.beginn_berechnung or emp.fecha_alta
    fuera_de_rango = False
    es_futuro = mes_inicio > hoy
    if inicio_calculo and mes_fin < inicio_calculo:
        fuera_de_rango = True
    elif emp.fecha_baja and mes_inicio > emp.fecha_baja:
        fuera_de_rango = True
    elif es_futuro:
        # Mes futuro: no calculamos planificadas hasta que llegue
        fuera_de_rango = True

    # Calcular valores
    if fuera_de_rango:
        horas_planificadas = Decimal("0.00")
        horas_reales = Decimal("0.00")
        horas_ausencia = Decimal("0.00")
        saldo_mes = Decimal("0.00")
        # Meses futuros / fuera de alta-baja: no arrastrar carryover
        carryover_anterior = Decimal("0.00")
        saldo_acumulado = Decimal("0.00")
    else:
        dias_mes = monthrange(anio, mes)[1]
        festivos = _festivos_mes(db, anio, mes)

        # Tagessollzeit = horas mensuales / días laborables ponderados del mes completo
        # (24.12 y 31.12 cuentan 0.5). Así la Sollzeit del mes completo = monthly_hours.
        soll_mes = _sollzeit_ponderada(mes_inicio, date(anio, mes, dias_mes), festivos)
        tagessollzeit = (
            (Decimal(str(emp.monthly_hours)) / soll_mes).quantize(Decimal("0.0001"))
            if soll_mes > 0 else Decimal("0")
        )

        # Rango efectivo (alta / baja / mes en curso)
        primer_dia_efectivo = mes_inicio
        if inicio_calculo and inicio_calculo.year == anio and inicio_calculo.month == mes:
            primer_dia_efectivo = inicio_calculo
        ultimo_dia_efectivo = date(anio, mes, dias_mes)
        if emp.fecha_baja and emp.fecha_baja.year == anio and emp.fecha_baja.month == mes:
            ultimo_dia_efectivo = emp.fecha_baja
        if mes_inicio.year == hoy.year and mes_inicio.month == hoy.month:
            if hoy < ultimo_dia_efectivo:
                ultimo_dia_efectivo = hoy

        # Horas planificadas = Sollzeit ponderada de los días laborables del rango
        soll_rango = _sollzeit_ponderada(primer_dia_efectivo, ultimo_dia_efectivo, festivos)
        horas_planificadas = (tagessollzeit * soll_rango).quantize(Decimal("0.01"))

        # Reales = fichajes (trabajo efectivo). Ausencia = crédito 'auf Sollzeit auffüllen'.
        horas_reales = _get_horas_reales_mes(db, empleado_id, anio, mes)
        horas_ausencia = _credito_ausencias_mes(
            db, empleado_id, primer_dia_efectivo, ultimo_dia_efectivo,
            festivos, tagessollzeit
        )
        saldo_mes = (horas_reales + horas_ausencia - horas_planificadas).quantize(Decimal("0.01"))
        carryover_anterior = _get_carryover_anterior(db, empleado_id, anio, mes)
        saldo_acumulado = (saldo_mes + carryover_anterior).quantize(Decimal("0.01"))

    # ⚠️ Sin Stundenkappung (requisito Personalabteilung): saldo_final = saldo_acumulado.
    kappung = None
    kappung_aplicada = False
    horas_cortadas = Decimal("0.00")
    saldo_final = saldo_acumulado

    now = datetime.utcnow()

    # Para meses fuera de rango (futuros, antes del inicio, después de baja)
    # no guardamos registro: serían 0/0/0 inútiles y ensucian el carryover futuro.
    # Devolvemos un dict transitorio sin persistir.
    if fuera_de_rango:
        if existente and not existente.cerrado:
            db.delete(existente)
            db.commit()
        ficticio = SaldoHorasMensual(
            empleado_id=empleado_id,
            anio=anio,
            mes=mes,
            horas_planificadas=horas_planificadas,
            horas_reales=horas_reales,
            horas_ausencia=horas_ausencia,
            saldo_mes=saldo_mes,
            carryover_anterior=carryover_anterior,
            saldo_acumulado=saldo_acumulado,
            limite_kappung=kappung,
            saldo_final=saldo_final,
            kappung_aplicada=False,
            horas_cortadas=Decimal("0.00"),
            cerrado=False,
            calculado_en=now,
        )
        return _saldo_to_dict(ficticio, emp)

    if existente:
        existente.horas_planificadas = horas_planificadas
        existente.horas_reales = horas_reales
        existente.horas_ausencia = horas_ausencia
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
            horas_ausencia=horas_ausencia,
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
        "horas_ausencia": float(s.horas_ausencia or 0),
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
