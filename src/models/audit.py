"""
Audit Log — registro de todos los cambios administrativos (HG-Plan G)
Interaction Log — registro profundo de cada interacción de usuario
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, Index
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


class InteractionLog(Base):
    """
    Log profundo de cada interacción del usuario con la interfaz.
    Registra clics, navegación, llamadas API, selecciones.
    Pensado para depuración forense y análisis de uso.
    """
    __tablename__ = "interaction_log"
    __table_args__ = (
        Index("ix_interaction_log_ts", "timestamp"),
        Index("ix_interaction_log_user", "user_nick"),
        Index("ix_interaction_log_page", "page"),
        {"schema": "hagemann"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Quién
    user_nick = Column(String(100), nullable=True,
                       comment="Nick del usuario autenticado")
    employee_name = Column(String(200), nullable=True,
                           comment="Nombre del empleado seleccionado (terminal)")
    employee_id = Column(String(50), nullable=True,
                         comment="ID del empleado (terminal)")

    # Qué
    action = Column(String(50), nullable=False,
                    comment="click | navigate | api_call | select | login | logout | error")
    target = Column(String(500), nullable=True,
                    comment="Texto/ID del elemento: botón, link, endpoint")
    detail = Column(Text, nullable=True,
                    comment="Detalle extra: respuesta API, valor seleccionado, etc.")

    # Dónde
    page = Column(String(100), nullable=True,
                  comment="Página/pantalla: admin, terminal, dashboard, etc.")
    source = Column(String(20), nullable=True,
                    comment="admin | terminal")

    # Cuándo (timestamp del cliente, no del servidor)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow,
                       comment="Momento de la interacción (cliente)")
    server_ts = Column(DateTime, nullable=False, default=datetime.utcnow,
                       comment="Momento de recepción (servidor)")
