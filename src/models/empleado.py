"""
Modelo Empleado — compatible con nfc2 de RTR
Campos mapeados desde nfc2 para facilitar integración futura.
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Date, Time,
    Numeric, Text, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


# ---------------------------------------------------------------------------
# Zeitgruppe — reglas de cálculo horario
# ---------------------------------------------------------------------------
class Zeitgruppe(Base):
    """
    Grupo horario: define cómo se calculan las horas trabajadas.
    Tipos:
      GLEITZEIT  — horario flexible, inicio/fin = login/logout
      VERWALTUNG — como Gleitzeit pero tiempo cuenta solo a partir de hora_minima_inicio
      SCHICHT    — tiempo cuenta desde inicio del turno planificado
    """
    __tablename__ = "zeitgruppen"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(150), nullable=False, unique=True,
                    comment="Nombre visible (ej: Gleitzeit Velten)")
    descripcion = Column(String(500), nullable=True)
    tipo = Column(String(20), nullable=False, default="GLEITZEIT",
                  comment="GLEITZEIT | VERWALTUNG | SCHICHT")
    hora_minima_inicio = Column(Time, nullable=True,
                                comment="Solo VERWALTUNG: tiempo no cuenta antes de esta hora")
    usar_inicio_turno = Column(Boolean, nullable=False, default=False,
                               comment="True → tiempo cuenta desde inicio del turno planificado")
    rotacion_semanal = Column(Boolean, nullable=False, default=False,
                              comment="True → turnos rotan por semana (BMB Schichten)")
    activo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    empleados = relationship("Empleado", back_populates="zeitgruppe")


class Empleado(Base):
    """Empleado de Hagemann"""

    __tablename__ = "empleados"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # --- IDs ---
    id_nummer = Column(Integer, unique=True, nullable=False, index=True,
                       comment="Systemnummer (= nfc2.id_nummer en RTR)")
    personalnummer = Column(Integer, nullable=True, unique=True,
                            comment="Personalnummer (separado de Systemnummer)")
    benutzer_id = Column(Integer, nullable=True, unique=True,
                         comment="Benutzer-ID para login en terminal")

    # --- Datos personales ---
    nombre = Column(String(200), nullable=False,
                    comment="Vorname")
    apellido = Column(String(200), nullable=True,
                      comment="Nachname")
    nfc_tag = Column(String(150), nullable=True,
                     comment="Transponder-ID / UID del tag NFC")
    keytag = Column(String(50), nullable=True,
                    comment="ID tarjeta badge (= nfc2.keytag)")

    # --- Datos laborales ---
    grupo_id = Column(UUID(as_uuid=True),
                      ForeignKey("hagemann.grupos.id"), nullable=True,
                      comment="Abteilung / departamento principal")
    kostenstelle_id = Column(UUID(as_uuid=True),
                             ForeignKey("hagemann.centros_coste.id"), nullable=True,
                             comment="Kostenstelle por defecto del empleado")
    zeitgruppe_id = Column(UUID(as_uuid=True),
                           ForeignKey("hagemann.zeitgruppen.id"), nullable=True,
                           comment="Grupo horario (Gleitzeit, Schicht, Verwaltung)")
    monthly_hours = Column(Integer, nullable=False, default=160,
                           comment="Horas mensuales según contrato")
    salary_hour = Column(Numeric(8, 2), nullable=True,
                         comment="Salario por hora")
    email = Column(String(150), nullable=True)
    telefono = Column(String(50), nullable=True)

    # --- Campos Hagemann Excel ---
    beginn_berechnung = Column(Date, nullable=True,
                               comment="Beginn der Berechnung — inicio cálculo de horas")
    mandat = Column(String(100), nullable=True, default="<Keine>",
                    comment="Mandat (futuro)")
    firmenbereich = Column(String(100), nullable=True, default="<Keine>",
                           comment="Firmenbereich (futuro)")

    # --- Stellvertretung ---
    stellvertreter_id = Column(UUID(as_uuid=True),
                               ForeignKey("hagemann.empleados.id"),
                               nullable=True,
                               comment="Stv. Schichtführer asignado como suplente")
    stellvertretung_hasta = Column(Date, nullable=True,
                                   comment="Fecha fin de la sustitución activa")

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
    kostenstelle = relationship("CentroCoste", foreign_keys=[kostenstelle_id])
    zeitgruppe = relationship("Zeitgruppe", back_populates="empleados")
    fichajes = relationship("Fichaje", back_populates="empleado")
    stellvertreter = relationship(
        "Empleado",
        foreign_keys=[stellvertreter_id],
        primaryjoin="Empleado.stellvertreter_id == Empleado.id",
        uselist=False,
    )


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
