"""
Audit Log — registro de todos los cambios administrativos (HG-Plan G)
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from ..database import Base


class AuditLog(Base):
    """
    Log de auditoría para cada cambio en el sistema.
    Registra: quién, cuándo, qué entidad, qué acción, cambios exactos.
    """
    __tablename__ = "audit_log"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Quién
    usuario_id = Column(UUID(as_uuid=True),
                        ForeignKey("hagemann.usuarios.id"), nullable=True,
                        comment="Usuario que realizó la acción")
    usuario_nick = Column(String(100), nullable=True,
                          comment="Nick del usuario (denormalizado para lectura rápida)")

    # Qué
    accion = Column(String(50), nullable=False,
                    comment="CREATE | UPDATE | DELETE | LOGIN | IMPORT | EXPORT | BACKUP")
    entidad_tipo = Column(String(50), nullable=False,
                          comment="empleado | grupo | kostenstelle | fichaje | turno | vacacion | ...")
    entidad_id = Column(String(50), nullable=True,
                        comment="ID de la entidad afectada")
    entidad_label = Column(String(200), nullable=True,
                           comment="Nombre/label legible (ej: 'Michel Mühle')")

    # Detalle
    cambios = Column(JSONB, nullable=True,
                     comment='{"campo": {"antes": X, "despues": Y}}')
    descripcion = Column(Text, nullable=True,
                         comment="Descripción libre del cambio")

    # Cuándo
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
