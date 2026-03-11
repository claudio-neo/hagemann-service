"""
Modelo SolicitudCorreccion — Solicitudes de corrección de fichaje (HG-16)
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, DateTime, Text, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class SolicitudCorreccion(Base):
    """
    Solicitud de corrección de un fichaje.
    El empleado pide cambiar entrada, salida o descanso.
    Se aprueba via el sistema de 2 niveles (HG-17).
    Estado: PENDIENTE | APROBADA | RECHAZADA
    """
    __tablename__ = "solicitudes_correccion"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Fichaje a corregir
    fichaje_id = Column(UUID(as_uuid=True),
                        ForeignKey("hagemann.fichajes.id"), nullable=False)
    empleado_id = Column(UUID(as_uuid=True),
                         ForeignKey("hagemann.empleados.id"), nullable=False)

    # Valores originales (snapshot en el momento de la solicitud)
    original_entrada = Column(DateTime, nullable=True)
    original_salida = Column(DateTime, nullable=True)
    original_descanso_min = Column(Integer, nullable=True)

    # Valores solicitados por el empleado
    solicitada_entrada = Column(DateTime, nullable=True)
    solicitada_salida = Column(DateTime, nullable=True)
    solicitado_descanso_min = Column(Integer, nullable=True)

    motivo = Column(Text, nullable=False)
    solicitado_por = Column(String(100), nullable=True,
                            comment="Nick del usuario que crea la solicitud")
    fecha_solicitud = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Estado
    estado = Column(String(20), nullable=False, default="PENDIENTE",
                    comment="PENDIENTE | APROBADA | RECHAZADA")
    revisado_por = Column(String(100), nullable=True)
    fecha_revision = Column(DateTime, nullable=True)
    comentario_revision = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False,
                        default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relaciones
    fichaje = relationship("Fichaje", foreign_keys=[fichaje_id])
    empleado = relationship("Empleado", foreign_keys=[empleado_id])
