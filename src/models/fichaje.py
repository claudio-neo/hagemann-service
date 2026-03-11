"""
Modelo Fichaje + SegmentoTiempo
- Fichaje: compatible con working_control de RTR
- SegmentoTiempo: nuevo para Hagemann (asignación por centro de coste)
"""
import uuid
import enum
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, SmallInteger, Boolean, DateTime,
    ForeignKey, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class FuenteFichaje(str, enum.Enum):
    TABLET = "TABLET"
    MANUAL = "MANUAL"
    API = "API"


class Fichaje(Base):
    """
    Registro de jornada — compatible con working_control de RTR.

    Mapeo:
      working_control.imei      → dispositivo_id
      working_control.date_in   → fecha_entrada
      working_control.date_out  → fecha_salida
      working_control.time_rest → minutos_descanso
      working_control.time_total→ minutos_trabajados
      working_control.name      → (via empleado.nombre)
      working_control.fix       → correccion
      working_control.status_in → status_entrada
      working_control.status_out→ status_salida
      working_control.forced_close → cierre_forzado
    """

    __tablename__ = "fichajes"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Empleado
    empleado_id = Column(UUID(as_uuid=True),
                         ForeignKey("hagemann.empleados.id"),
                         nullable=False, index=True)

    # Dispositivo (= working_control.imei)
    dispositivo_id = Column(String(50), nullable=True,
                            comment="IMEI o token del tablet")

    # Tiempos
    fecha_entrada = Column(DateTime, nullable=False, index=True)
    fecha_salida = Column(DateTime, nullable=True,
                          comment="NULL = jornada abierta")
    minutos_descanso = Column(Integer, default=0)
    minutos_trabajados = Column(Integer, nullable=True,
                                comment="Calculado al cerrar: suma de segmentos")

    # Corrección / estado (compatibilidad RTR)
    correccion = Column(SmallInteger, default=0,
                        comment="Código de ajuste (= working_control.fix)")
    status_entrada = Column(SmallInteger, default=0,
                            comment="0=ok, 1=fuera de zona")
    status_salida = Column(SmallInteger, default=0)
    cierre_forzado = Column(Boolean, default=False,
                            comment="True si el sistema cerró automáticamente")

    # Fuente y metadata
    fuente = Column(String(20), default="TABLET")
    notas = Column(Text, nullable=True)
    modificado_por = Column(String(100), nullable=True,
                            comment="Nick del último usuario que editó")

    # Auditoría
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Relationships
    empleado = relationship("Empleado", back_populates="fichajes")
    segmentos = relationship("SegmentoTiempo", back_populates="fichaje",
                             order_by="SegmentoTiempo.inicio")


class SegmentoTiempo(Base):
    """
    Segmento de jornada asignado a un centro de coste.
    Una jornada (Fichaje) tiene N segmentos.

    Ejemplo:
      08:00-12:00 → Logistik (240 min)
      13:00-17:00 → Verwaltung (240 min)
    """

    __tablename__ = "segmentos_tiempo"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    fichaje_id = Column(UUID(as_uuid=True),
                        ForeignKey("hagemann.fichajes.id"),
                        nullable=False, index=True)
    empleado_id = Column(UUID(as_uuid=True),
                         ForeignKey("hagemann.empleados.id"),
                         nullable=False, index=True,
                         comment="Denormalizado para queries rápidas por empleado")
    centro_coste_id = Column(UUID(as_uuid=True),
                             ForeignKey("hagemann.centros_coste.id"),
                             nullable=False, index=True)

    inicio = Column(DateTime, nullable=False)
    fin = Column(DateTime, nullable=True,
                 comment="NULL = segmento activo")
    minutos = Column(Integer, nullable=True,
                     comment="Calculado al cerrar el segmento")

    creado_por = Column(String(100), default="system",
                        comment="system=fichaje automático, nick=corrección manual")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Relationships
    fichaje = relationship("Fichaje", back_populates="segmentos")
    centro_coste = relationship("CentroCoste", back_populates="segmentos")
