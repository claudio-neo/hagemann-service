"""
Modelo Usuario — autenticación interna de Hagemann (HG-13)
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime,
    ForeignKey, Text, Table, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


def ensure_columns(engine) -> None:
    """Migración idempotente: añade columnas nuevas a hagemann.usuarios."""
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE hagemann.usuarios "
            "ADD COLUMN IF NOT EXISTS terminal_mode VARCHAR(20) DEFAULT 'voll'"
        ))


# Tabla de relación N:M — un Gruppenadmin puede tener varios grupos asignados.
usuario_grupos = Table(
    "usuario_grupos",
    Base.metadata,
    Column("usuario_id", UUID(as_uuid=True),
           ForeignKey("hagemann.usuarios.id", ondelete="CASCADE"), primary_key=True),
    Column("grupo_id", UUID(as_uuid=True),
           ForeignKey("hagemann.grupos.id", ondelete="CASCADE"), primary_key=True),
    schema="hagemann",
)


"""
Roles del sistema Hagemann:
  1 = Admin              — Acceso completo sin restricciones (Personalabteilung)
  2 = Schichtführer      — Liberar horas, control horas, vista vacaciones equipo,
                           elegir suplente (Stellvertreter)
  3 = Stv. Schichtführer — Como Benutzer + actúa como Schichtführer al sustituir
  4 = Benutzer           — Login/Logout, Raucherpause, solicitar vacaciones/FZA,
                           ver propias horas/vacaciones
  5 = Gruppenadmin       — Como Admin pero limitado a una única Gruppe (grupo_id).
                           Solo ve/gestiona empleados de su grupo asignado.
"""
ROLE_ADMIN = 1
ROLE_SCHICHTFUEHRER = 2
ROLE_STV_SCHICHTFUEHRER = 3
ROLE_BENUTZER = 4
ROLE_GRUPPENADMIN = 5

ROLE_LABELS = {
    ROLE_ADMIN: "Admin",
    ROLE_SCHICHTFUEHRER: "Schichtführer",
    ROLE_STV_SCHICHTFUEHRER: "Stv. Schichtführer",
    ROLE_BENUTZER: "Benutzer",
    ROLE_GRUPPENADMIN: "Gruppenadmin",
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
                  comment="1=Admin, 2=Schichtführer, 3=Stv.Schichtführer, 4=Benutzer, 5=Gruppenadmin")

    # FK opcional al empleado
    empleado_id = Column(UUID(as_uuid=True),
                         ForeignKey("hagemann.empleados.id"),
                         nullable=True)

    activo = Column(Boolean, nullable=False, default=True)
    last_login = Column(DateTime, nullable=True)

    # Modo del terminal para usuarios-tablet:
    #   'voll'          → todas las funciones (Einloggen/Ausloggen/Raucherpause/…)
    #   'eingeschraenkt'→ solo KST-Wechsel + Urlaubsantrag + Stundenkonto (sin fichar)
    terminal_mode = Column(String(20), nullable=True, default="voll",
                           comment="voll | eingeschraenkt (terminales tablet)")

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False,
                        default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relaciones
    empleado = relationship("Empleado", foreign_keys=[empleado_id])
    # Grupos asignados (solo relevante para role=Gruppenadmin). Vacío = sin
    # restricción (Admin/Personalabteilung ve todo).
    grupos = relationship("Grupo", secondary=usuario_grupos, lazy="selectin")
