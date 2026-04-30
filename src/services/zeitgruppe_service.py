"""
Servicio de ajuste horario según Zeitgruppe — HG-Plan C

Reglas:
  GLEITZEIT  → sin ajuste, se registra la hora real
  VERWALTUNG → si entrada < hora_minima_inicio, se redondea al inicio
  SCHICHT    → si entrada < hora_inicio del turno planificado, se redondea al inicio del turno
"""
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID
from sqlalchemy.orm import Session

from ..models.empleado import Empleado, Zeitgruppe
from ..models.turno import PlanTurno, ModeloTurno


def aplicar_ajuste_zeitgruppe(
    ts: datetime,
    emp: Empleado,
    db: Session,
) -> tuple[datetime, Optional[str]]:
    """
    Ajusta el timestamp de entrada según la Zeitgruppe del empleado.

    Returns:
        (timestamp_ajustado, mensaje_ajuste | None)
    """
    if not emp.zeitgruppe_id:
        return ts, None

    zg = db.query(Zeitgruppe).filter(Zeitgruppe.id == emp.zeitgruppe_id).first()
    if not zg or not zg.activo:
        return ts, None

    if zg.tipo == "VERWALTUNG" and zg.hora_minima_inicio:
        # Si llega antes de la hora mínima, ajustar al inicio permitido
        hora_min = ts.replace(
            hour=zg.hora_minima_inicio.hour,
            minute=zg.hora_minima_inicio.minute,
            second=0, microsecond=0,
        )
        if ts < hora_min:
            delta = int((hora_min - ts).total_seconds() // 60)
            return hora_min, (
                f"Zeitgruppe '{zg.nombre}': Startzeit vor {zg.hora_minima_inicio.strftime('%H:%M')} Uhr "
                f"— Beginn auf {hora_min.strftime('%H:%M')} verschoben (+{delta} Min.)"
            )

    elif zg.tipo == "SCHICHT" and zg.usar_inicio_turno:
        # Buscar el turno planificado para hoy
        fecha_hoy = ts.date()
        plan = (
            db.query(PlanTurno)
            .filter(
                PlanTurno.empleado_id == emp.id,
                PlanTurno.fecha_plan == fecha_hoy,
            )
            .first()
        )
        if plan and plan.modelo_turno_id:
            modelo = db.query(ModeloTurno).filter(
                ModeloTurno.id == plan.modelo_turno_id
            ).first()
            if modelo and modelo.hora_inicio:
                hora_turno = ts.replace(
                    hour=modelo.hora_inicio.hour,
                    minute=modelo.hora_inicio.minute,
                    second=0, microsecond=0,
                )
                # Si el turno cruza medianoche y la hora de inicio es mayor que ahora
                # (ej: turno noche 22:00 y se ficha a las 21:50), ajustar igual
                if modelo.cruza_medianoche and ts.hour < 12:
                    # turno de noche anterior, no ajustar
                    return ts, None
                if ts < hora_turno:
                    delta = int((hora_turno - ts).total_seconds() // 60)
                    return hora_turno, (
                        f"Zeitgruppe '{zg.nombre}': Schichtbeginn {modelo.hora_inicio.strftime('%H:%M')} "
                        f"— Beginn auf {hora_turno.strftime('%H:%M')} verschoben (+{delta} Min.)"
                    )

    return ts, None


def es_stellvertretung_activa(emp: Empleado, fecha: datetime) -> bool:
    """
    Devuelve True si el empleado tiene una sustitución activa en la fecha dada.
    Un Stv. Schichtführer con stellvertretung activa tiene permisos de Schichtführer.
    """
    if not emp.stellvertreter_id:
        return False
    if not emp.stellvertretung_hasta:
        return True  # Sin fecha fin → activa indefinidamente
    return fecha.date() <= emp.stellvertretung_hasta


def calcular_minutos_rauch_descontables(fichaje_id: UUID, db: Session) -> int:
    """Suma los minutos de pausen descontables de un fichaje."""
    from ..models.pausa import Pausa
    from sqlalchemy import func
    result = db.query(
        func.coalesce(func.sum(Pausa.minutos), 0)
    ).filter(
        Pausa.fichaje_id == fichaje_id,
        Pausa.descontado == True,
        Pausa.fin.isnot(None),
    ).scalar()
    return result or 0
