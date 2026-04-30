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
    ForeignKey, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


# ========== ENUMS ==========

class TipoFestivo(str, enum.Enum):
    NACIONAL = "NACIONAL"
    REGIONAL = "REGIONAL"


class TipoAusencia(str, enum.Enum):
    VACACIONES = "VACACIONES"           # Urlaub
    BAJA_MEDICA = "BAJA_MEDICA"         # Krankmeldung
    ASUNTOS_PROPIOS = "ASUNTOS_PROPIOS" # Sonstiges
    PERMISO_RETRIBUIDO = "PERMISO_RETRIBUIDO"
    FZA = "FZA"                         # Freizeitausgleich (compensación horas extra)


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
