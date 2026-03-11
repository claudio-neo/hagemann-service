"""
Modelo AprobacionLog — Sistema genérico de aprobaciones 2 niveles (HG-17)
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from ..database import Base


class AprobacionLog(Base):
    """
    Registro de aprobación en 2 niveles para cualquier entidad del sistema.
    - Nivel 1: Abteilungsleiter (jefe de departamento)
    - Nivel 2: Admin (aprobación final)
    
    Acciones posibles: PENDIENTE | PROPUESTA | RECHAZADA
    Estado final:      PENDIENTE | PROPUESTA | APROBADA | RECHAZADA
    """
    __tablename__ = "aprobaciones_log"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Entidad referenciada (genérico)
    tipo_entidad = Column(String(100), nullable=False, index=True,
                          comment="Ej: correccion_fichaje, solicitud_vacacion")
    entidad_id = Column(String(100), nullable=False, index=True,
                        comment="UUID de la entidad referenciada (como string)")

    # Nivel 1 — Abteilungsleiter
    nivel1_usuario = Column(String(100), nullable=True,
                            comment="Nick del usuario nivel 1")
    nivel1_accion = Column(String(20), nullable=False, default="PENDIENTE",
                           comment="PENDIENTE | PROPUESTA | RECHAZADA")
    nivel1_fecha = Column(DateTime, nullable=True)
    nivel1_comentario = Column(Text, nullable=True)

    # Nivel 2 — Admin
    nivel2_usuario = Column(String(100), nullable=True,
                            comment="Nick del usuario nivel 2")
    nivel2_accion = Column(String(20), nullable=True,
                           comment="PENDIENTE | APROBADA | RECHAZADA")
    nivel2_fecha = Column(DateTime, nullable=True)
    nivel2_comentario = Column(Text, nullable=True)

    # Estado consolidado
    estado_final = Column(String(20), nullable=False, default="PENDIENTE",
                          comment="PENDIENTE | PROPUESTA | APROBADA | RECHAZADA")

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False,
                        default=datetime.utcnow, onupdate=datetime.utcnow)
