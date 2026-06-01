"""
Modelos de Vacaciones y Festivos — Hagemann
- Festivos (Feiertage Sachsen)
- PeriodoVacaciones (saldo anual por empleado)
- SolicitudVacaciones (workflow PENDIENTE→PROPUESTA→APROBADA/RECHAZADA)
- LimiteVacaciones (máx. ausencias simultáneas por grupo)
"""
import uuid
import enum
from datetime import datetime, date
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Date,
    ForeignKey, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


# ========== ENUMS ==========

class TipoFestivo(str, enum.Enum):
    NACIONAL = "NACIONAL"
    REGIONAL = "REGIONAL"


class TipoAusencia(str, enum.Enum):
    # ── Existentes ──────────────────────────────────────────────────────────
    VACACIONES = "VACACIONES"                       # Urlaub
    BAJA_MEDICA = "BAJA_MEDICA"                      # Krankheit (mit Schein)
    ASUNTOS_PROPIOS = "ASUNTOS_PROPIOS"             # Sonstiges
    PERMISO_RETRIBUIDO = "PERMISO_RETRIBUIDO"
    FZA = "FZA"                                     # Freizeitausgleich
    # ── Nuevos (requisitos Personalabteilung 2026-06) ──────────────────────
    ARBEITSUNFALL = "ARBEITSUNFALL"
    ARZT_GANG = "ARZT_GANG"
    BERUFSSCHULE = "BERUFSSCHULE"
    ELTERNZEIT = "ELTERNZEIT"
    FREISTELLUNG = "FREISTELLUNG"
    UNENTSCHULDIGT = "UNENTSCHULDIGT"               # Unentschuldigtes Fehlen
    HOMEOFFICE = "HOMEOFFICE"
    KRANKHEIT_KIND = "KRANKHEIT_KIND"
    KRANKHEIT_OHNE_SCHEIN = "KRANKHEIT_OHNE_SCHEIN"
    SONDERURLAUB = "SONDERURLAUB"                   # Umzug, Tod, Hochzeit…
    WEITERBILDUNG = "WEITERBILDUNG"
    UNBEZAHLTER_URLAUB = "UNBEZAHLTER_URLAUB"
    HH_MODELL = "HH_MODELL"


class SaldoWirkung(str, enum.Enum):
    """Efecto de la ausencia sobre el Saldo de horas."""
    AUFFUELLEN = "AUFFUELLEN"          # Acredita la Sollzeit del día (saldo neutro)
    UNTERBRECHUNG = "UNTERBRECHUNG"    # No acredita nada (consume saldo / déficit)


# Clasificación de cada tipo. Por defecto AUFFUELLEN; las interrupciones explícitas
# (FZA y falta injustificada) no acreditan Sollzeit.
SALDO_WIRKUNG = {
    TipoAusencia.FZA: SaldoWirkung.UNTERBRECHUNG,
    TipoAusencia.UNENTSCHULDIGT: SaldoWirkung.UNTERBRECHUNG,
}


def saldo_wirkung(tipo) -> "SaldoWirkung":
    """Devuelve el efecto sobre el saldo de un tipo de ausencia (default AUFFUELLEN)."""
    try:
        tipo = TipoAusencia(tipo)
    except ValueError:
        return SaldoWirkung.AUFFUELLEN
    return SALDO_WIRKUNG.get(tipo, SaldoWirkung.AUFFUELLEN)


# Etiquetas alemanas para la UI / informes.
TIPO_AUSENCIA_LABELS = {
    TipoAusencia.VACACIONES: "Urlaub",
    TipoAusencia.BAJA_MEDICA: "Krankheit (mit Schein)",
    TipoAusencia.ASUNTOS_PROPIOS: "Sonstiges",
    TipoAusencia.PERMISO_RETRIBUIDO: "Bezahlte Freistellung",
    TipoAusencia.FZA: "FZA (Freizeitausgleich)",
    TipoAusencia.ARBEITSUNFALL: "Arbeitsunfall",
    TipoAusencia.ARZT_GANG: "Arzt-Gang",
    TipoAusencia.BERUFSSCHULE: "Berufsschule",
    TipoAusencia.ELTERNZEIT: "Elternzeit",
    TipoAusencia.FREISTELLUNG: "Freistellung",
    TipoAusencia.UNENTSCHULDIGT: "Unentschuldigtes Fehlen",
    TipoAusencia.HOMEOFFICE: "Homeoffice",
    TipoAusencia.KRANKHEIT_KIND: "Krankheit Kind",
    TipoAusencia.KRANKHEIT_OHNE_SCHEIN: "Krankheit ohne Schein",
    TipoAusencia.SONDERURLAUB: "Sonderurlaub",
    TipoAusencia.WEITERBILDUNG: "Weiterbildung",
    TipoAusencia.UNBEZAHLTER_URLAUB: "Unbezahlter Urlaub",
    TipoAusencia.HH_MODELL: "HH-Modell",
}


# Tipos considerados "Krankmeldung" (disparan aviso a la Personalabteilung).
KRANKHEIT_TIPOS = {
    TipoAusencia.BAJA_MEDICA,
    TipoAusencia.KRANKHEIT_OHNE_SCHEIN,
    TipoAusencia.KRANKHEIT_KIND,
}


def es_krankmeldung(tipo) -> bool:
    try:
        return TipoAusencia(tipo) in KRANKHEIT_TIPOS
    except ValueError:
        return False


class EstadoSolicitud(str, enum.Enum):
    PENDIENTE = "PENDIENTE"       # Enviada por empleado, sin acción
    PROPUESTA = "PROPUESTA"       # Abteilungsleiter propone/valida (nivel 1)
    APROBADA = "APROBADA"         # Admin aprueba (nivel 2)
    RECHAZADA = "RECHAZADA"       # Rechazada en cualquier nivel


# ========== MODELOS ==========

class Festivo(Base):
    """
    Festivo oficial (Feiertag).
    Incluye nacionales alemanes y regionales de Sachsen.
    """

    __tablename__ = "festivos"
    __table_args__ = (
        UniqueConstraint("fecha", "bundesland", name="uq_festivo_fecha_bundesland"),
        {"schema": "hagemann"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fecha = Column(Date, nullable=False, index=True)
    nombre = Column(String(200), nullable=False,
                    comment="Nombre oficial del festivo (alemán)")
    bundesland = Column(String(50), nullable=False, default="DE",
                        comment="'DE' para nacional, 'SN' para Sachsen, etc.")
    tipo = Column(String(20), nullable=False, default=TipoFestivo.NACIONAL,
                  comment="NACIONAL o REGIONAL")
    activo = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class PeriodoVacaciones(Base):
    """
    Saldo de vacaciones de un empleado para un año.
    dias_contrato: días que tiene según contrato
    dias_extra: días adicionales (antigüedad, etc.)
    dias_usados: calculado dinámicamente desde solicitudes aprobadas
    """

    __tablename__ = "periodos_vacaciones"
    __table_args__ = (
        UniqueConstraint("empleado_id", "anio", name="uq_periodo_empleado_anio"),
        {"schema": "hagemann"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    empleado_id = Column(UUID(as_uuid=True),
                         ForeignKey("hagemann.empleados.id"),
                         nullable=False, index=True)
    anio = Column(Integer, nullable=False, index=True,
                  comment="Año del periodo vacacional")
    dias_contrato = Column(Integer, nullable=False, default=30,
                           comment="Días de vacaciones según contrato")
    dias_extra = Column(Integer, nullable=False, default=0,
                        comment="Días adicionales (antigüedad, acuerdo, etc.)")
    dias_usados = Column(Integer, nullable=False, default=0,
                         comment="Días consumidos — actualizado al aprobar solicitudes")

    notas = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    empleado = relationship("Empleado", backref="periodos_vacaciones")
    solicitudes = relationship("SolicitudVacaciones",
                               back_populates="periodo",
                               order_by="SolicitudVacaciones.fecha_inicio")


class SolicitudVacaciones(Base):
    """
    Solicitud de ausencia con workflow de aprobación 2 niveles.

    Workflow:
      Empleado crea → PENDIENTE
      Abteilungsleiter propone → PROPUESTA
      Admin aprueba → APROBADA (descuenta días del periodo)
      Cualquier nivel puede rechazar → RECHAZADA
    """

    __tablename__ = "solicitudes_vacaciones"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    empleado_id = Column(UUID(as_uuid=True),
                         ForeignKey("hagemann.empleados.id"),
                         nullable=False, index=True)
    periodo_id = Column(UUID(as_uuid=True),
                        ForeignKey("hagemann.periodos_vacaciones.id"),
                        nullable=False, index=True,
                        comment="Periodo vacacional al que se imputa")

    # Fechas y días
    fecha_inicio = Column(Date, nullable=False)
    fecha_fin = Column(Date, nullable=False)
    dias = Column(Integer, nullable=False,
                  comment="Días laborables solicitados (excluyendo fines de semana y festivos)")
    medio_dia = Column(Boolean, nullable=False, default=False,
                       comment="½ Tag: cada día cuenta como media jornada (media Sollzeit)")

    # Tipo de ausencia
    tipo_ausencia = Column(String(30), nullable=False,
                           default=TipoAusencia.VACACIONES,
                           comment="VACACIONES, BAJA_MEDICA, ASUNTOS_PROPIOS, PERMISO_RETRIBUIDO")

    # Workflow
    estado = Column(String(20), nullable=False, default=EstadoSolicitud.PENDIENTE,
                    index=True, comment="PENDIENTE→PROPUESTA→APROBADA/RECHAZADA")

    # Aprobación nivel 1 (Abteilungsleiter)
    aprobado_por_nivel1 = Column(String(100), nullable=True,
                                 comment="Nick/nombre del Abteilungsleiter que propuso")
    fecha_nivel1 = Column(DateTime, nullable=True)
    notas_nivel1 = Column(Text, nullable=True)

    # Aprobación nivel 2 (Admin)
    aprobado_por_nivel2 = Column(String(100), nullable=True,
                                 comment="Nick/nombre del Admin que aprobó o rechazó")
    fecha_nivel2 = Column(DateTime, nullable=True)
    notas_nivel2 = Column(Text, nullable=True)

    # Motivo rechazo (si aplica)
    motivo_rechazo = Column(Text, nullable=True)

    notas = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    empleado = relationship("Empleado", backref="solicitudes_vacaciones")
    periodo = relationship("PeriodoVacaciones", back_populates="solicitudes")


def ensure_columns(engine) -> None:
    """Migración idempotente para columnas añadidas a tablas preexistentes."""
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE hagemann.solicitudes_vacaciones "
            "ADD COLUMN IF NOT EXISTS medio_dia BOOLEAN NOT NULL DEFAULT FALSE"
        ))


class LimiteVacaciones(Base):
    """
    Límite de ausencias simultáneas para un grupo en un periodo.
    Evita que más de N personas del mismo departamento estén ausentes a la vez.
    """

    __tablename__ = "limites_vacaciones"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    grupo_id = Column(UUID(as_uuid=True),
                      ForeignKey("hagemann.grupos.id"),
                      nullable=False, index=True)
    fecha_inicio = Column(Date, nullable=False,
                          comment="Inicio del periodo donde aplica el límite")
    fecha_fin = Column(Date, nullable=False,
                       comment="Fin del periodo donde aplica el límite")
    max_ausencias = Column(Integer, nullable=False, default=1,
                           comment="Máximo de personas ausentes simultáneamente en este grupo")

    activo = Column(Boolean, default=True)
    descripcion = Column(String(250), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    grupo = relationship("Grupo", backref="limites_vacaciones")
