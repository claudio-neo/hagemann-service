"""
Pausas durante la jornada — Raucherpause, Mittagspause, etc.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class Pausa(Base):
    """
    Registro de una pausa dentro de una jornada.
    Tipos: RAUCH (Raucherpause), MITTAG (Mittagspause), SONSTIG (Sonstiges)
    """
    __tablename__ = "pausen"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    fichaje_id = Column(UUID(as_uuid=True),
                        ForeignKey("hagemann.fichajes.id"), nullable=False)
    empleado_id = Column(UUID(as_uuid=True),
                         ForeignKey("hagemann.empleados.id"), nullable=False)

    tipo = Column(String(20), nullable=False, default="RAUCH",
                  comment="RAUCH | MITTAG | SONSTIG")
    inicio = Column(DateTime, nullable=False)
    fin = Column(DateTime, nullable=True, comment="NULL = pausa aún abierta")
    minutos = Column(Integer, nullable=True,
                     comment="Duración en minutos (calculado al cerrar)")
    descontado = Column(Boolean, default=True,
                        comment="True = esta pausa se descuenta del tiempo trabajado")
    notas = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    fichaje = relationship("Fichaje", foreign_keys=[fichaje_id])
    empleado = relationship("Empleado", foreign_keys=[empleado_id])
