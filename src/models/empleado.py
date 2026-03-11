"""
Modelo Empleado — compatible con nfc2 de RTR
Campos mapeados desde nfc2 para facilitar integración futura.
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Date,
    Numeric, Text, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class Empleado(Base):
    """Empleado de Hagemann"""

    __tablename__ = "empleados"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # --- Mapeo directo desde nfc2 de RTR ---
    id_nummer = Column(Integer, unique=True, nullable=False, index=True,
                       comment="Número de personal (= nfc2.id_nummer en RTR)")
    nombre = Column(String(200), nullable=False,
                    comment="Nombre completo (= nfc2.bl1_vor + bl1_nam)")
    apellido = Column(String(200), nullable=True)
    nfc_tag = Column(String(150), nullable=True,
                     comment="UID del tag NFC (= nfc2.id_nfc)")
    keytag = Column(String(50), nullable=True,
                    comment="ID tarjeta badge (= nfc2.keytag)")

    # --- Datos laborales ---
    grupo_id = Column(UUID(as_uuid=True),
                      ForeignKey("hagemann.grupos.id"), nullable=True,
                      comment="Grupo/departamento principal")
    monthly_hours = Column(Integer, nullable=False, default=160,
                           comment="Horas mensuales según contrato")
    salary_hour = Column(Numeric(8, 2), nullable=True,
                         comment="Salario por hora")
    email = Column(String(150), nullable=True)
    telefono = Column(String(50), nullable=True)

    # --- Estado ---
    activo = Column(Boolean, default=True, nullable=False)
    fecha_alta = Column(Date, nullable=True)
    fecha_baja = Column(Date, nullable=True)

    # --- Auditoría ---
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Relationships
    grupo = relationship("Grupo", back_populates="empleados")
    fichajes = relationship("Fichaje", back_populates="empleado")


class Grupo(Base):
    """Grupo/departamento — compatible con RTR grupos"""

    __tablename__ = "grupos"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(250), nullable=False, unique=True)
    descripcion = Column(String(500), nullable=True)
    activo = Column(Boolean, default=True)
    orden = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)

    empleados = relationship("Empleado", back_populates="grupo")


class CentroCoste(Base):
    """Centro de coste para imputación de horas (nuevo para Hagemann)"""

    __tablename__ = "centros_coste"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    codigo = Column(String(20), unique=True, nullable=False,
                    comment="Código contable (ej: 4100, 4200)")
    nombre = Column(String(150), nullable=False,
                    comment="Nombre del centro (ej: Logistik, Verwaltung)")
    descripcion = Column(String(250), nullable=True)
    color = Column(String(7), nullable=True,
                   comment="Color hex para UI (ej: #3B82F6)")
    activo = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    segmentos = relationship("SegmentoTiempo", back_populates="centro_coste")
