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


"""
Roles del sistema Hagemann:
  1 = Admin              — Acceso completo sin restricciones
  2 = Schichtführer      — Liberar horas, control horas, vista vacaciones equipo,
                           elegir suplente (Stellvertreter)
  3 = Stv. Schichtführer — Como Benutzer + actúa como Schichtführer al sustituir
  4 = Benutzer           — Login/Logout, Raucherpause, solicitar vacaciones/FZA,
                           ver propias horas/vacaciones
"""
ROLE_ADMIN = 1
ROLE_SCHICHTFUEHRER = 2
ROLE_STV_SCHICHTFUEHRER = 3
ROLE_BENUTZER = 4

ROLE_LABELS = {
    ROLE_ADMIN: "Admin",
    ROLE_SCHICHTFUEHRER: "Schichtführer",
    ROLE_STV_SCHICHTFUEHRER: "Stv. Schichtführer",
    ROLE_BENUTZER: "Benutzer",
}


class Usuario(Base):
    """
    Usuario del sistema Hagemann.
    Roles: 1=Admin, 2=Schichtführer, 3=Stv. Schichtführer, 4=Benutzer
    """
    __tablename__ = "usuarios"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nick = Column(String(100), nullable=False, unique=True, index=True)
    email = Column(String(255), nullable=True, unique=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(Integer, nullable=False, default=ROLE_BENUTZER,
                  comment="1=Admin, 2=Schichtführer, 3=Stv.Schichtführer, 4=Benutzer")

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
