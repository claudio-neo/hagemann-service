"""
Modelo Usuario — autenticación interna de Hagemann (HG-13)
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime,
    ForeignKey, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class Usuario(Base):
    """
    Usuario del sistema Hagemann.
    Roles: 1=Admin, 2=Abteilungsleiter (jefe dpto), 3=Mitarbeiter (empleado)
    """
    __tablename__ = "usuarios"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nick = Column(String(100), nullable=False, unique=True, index=True)
    email = Column(String(255), nullable=True, unique=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(Integer, nullable=False, default=3,
                  comment="1=Admin, 2=Abteilungsleiter, 3=Mitarbeiter")

    # FK opcional al empleado
    empleado_id = Column(UUID(as_uuid=True),
                         ForeignKey("hagemann.empleados.id"),
                         nullable=True)

    activo = Column(Boolean, nullable=False, default=True)
    last_login = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False,
                        default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relaciones
    empleado = relationship("Empleado", foreign_keys=[empleado_id])
