"""
Modelos de Turno — HG-14 (Schichtmodelle) y HG-15 (Planificación)
"""
import uuid
from datetime import datetime, time
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Date, Time,
    Float, ForeignKey, UniqueConstraint, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class ModeloTurno(Base):
    """
    Modelo de turno configurado (HG-14).
    Ej: Frühschicht F 06:00-14:00, Nachtschicht N 22:00-06:00
    """
    __tablename__ = "modelos_turno"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(200), nullable=False)
    codigo = Column(String(20), nullable=False, unique=True, index=True,
                    comment="Código corto, ej: F, S, N, NS, X")

    hora_inicio = Column(Time, nullable=True,
                         comment="Hora de inicio del turno (None si es Frei)")
    hora_fin = Column(Time, nullable=True,
                      comment="Hora de fin del turno (None si es Frei)")
    minutos_pausa = Column(Integer, nullable=False, default=0,
                           comment="Minutos de pausa/descanso")
    horas_netas = Column(Float, nullable=False, default=0.0,
                         comment="Horas netas trabajadas (calculado)")
    cruza_medianoche = Column(Boolean, nullable=False, default=False,
                              comment="True si el turno cruza las 00:00")

    color = Column(String(20), nullable=True, default="#607D8B",
                   comment="Color hex para el calendario")
    activo = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(100), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False,
                        default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relaciones
    planes = relationship("PlanTurno", back_populates="modelo_turno")


class PlanTurno(Base):
    """
    Plan de turno para un empleado en una fecha concreta (HG-15).
    """
    __tablename__ = "planes_turno"
    __table_args__ = (
        UniqueConstraint("empleado_id", "fecha_plan",
                         name="uq_plan_empleado_fecha"),
        {"schema": "hagemann"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empleado_id = Column(UUID(as_uuid=True),
                         ForeignKey("hagemann.empleados.id"), nullable=False)
    modelo_turno_id = Column(UUID(as_uuid=True),
                              ForeignKey("hagemann.modelos_turno.id"), nullable=True,
                              comment="None = Frei / día libre sin modelo")
    fecha_plan = Column(Date, nullable=False, index=True)

    # Real vs planificado
    entrada_real = Column(DateTime, nullable=True)
    salida_real = Column(DateTime, nullable=True)

    # Estado: 0=planificado, 1=cumplido, 2=ausente, 3=modificado
    estado = Column(Integer, nullable=False, default=0)
    tipo_ausencia = Column(String(50), nullable=True,
                           comment="Krankheit, Urlaub, Sonstiges...")
    nota = Column(Text, nullable=True)
    created_by = Column(String(100), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False,
                        default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relaciones
    empleado = relationship("Empleado", foreign_keys=[empleado_id])
    modelo_turno = relationship("ModeloTurno", back_populates="planes")
