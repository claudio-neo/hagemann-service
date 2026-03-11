"""
Modelo SaldoHorasMensual — Hagemann
Almacena el cierre mensual de horas por empleado.
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, Integer, Boolean, DateTime, Numeric,
    ForeignKey, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..database import Base


class SaldoHorasMensual(Base):
    """
    Cierre mensual de horas para un empleado.

    Lógica:
      - planificado: empleado.monthly_hours (horas/mes según contrato)
      - real: suma de minutos_trabajados de fichajes del mes / 60
      - saldo_mes: real - planificado
      - carryover_anterior: saldo acumulado del mes anterior (después de Stundenkappung)
      - saldo_acumulado: saldo_mes + carryover_anterior (antes de Kappung)
      - saldo_final: min(saldo_acumulado, limite_kappung)  ← Stundenkappung aplicada
    """

    __tablename__ = "saldo_horas_mensual"
    __table_args__ = (
        UniqueConstraint("empleado_id", "anio", "mes",
                         name="uq_saldo_empleado_anio_mes"),
        {"schema": "hagemann"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    empleado_id = Column(UUID(as_uuid=True),
                         ForeignKey("hagemann.empleados.id"),
                         nullable=False, index=True)
    anio = Column(Integer, nullable=False)
    mes = Column(Integer, nullable=False, comment="1=Enero … 12=Diciembre")

    # Horas (almacenadas como decimales para precisión)
    horas_planificadas = Column(Numeric(8, 2), nullable=False,
                                comment="Horas según contrato (monthly_hours)")
    horas_reales = Column(Numeric(8, 2), nullable=False, default=0,
                          comment="Horas efectivamente trabajadas según fichajes")
    saldo_mes = Column(Numeric(8, 2), nullable=False, default=0,
                       comment="horas_reales - horas_planificadas")

    # Carryover y acumulado
    carryover_anterior = Column(Numeric(8, 2), nullable=False, default=0,
                                comment="Saldo final del mes anterior")
    saldo_acumulado = Column(Numeric(8, 2), nullable=False, default=0,
                             comment="saldo_mes + carryover_anterior (antes de Kappung)")
    limite_kappung = Column(Numeric(8, 2), nullable=True,
                            comment="Límite de horas extra acumuladas (Stundenkappung). NULL=sin límite")
    saldo_final = Column(Numeric(8, 2), nullable=False, default=0,
                         comment="Saldo acumulado con Kappung aplicada (pasa al mes siguiente)")
    kappung_aplicada = Column(Boolean, default=False,
                              comment="True si se recortó el saldo por Stundenkappung")
    horas_cortadas = Column(Numeric(8, 2), nullable=False, default=0,
                            comment="Horas que se perdieron por Kappung")

    # Metadata
    cerrado = Column(Boolean, default=False,
                     comment="True = cierre oficial (no se recalcula automáticamente)")
    notas = Column(Text, nullable=True)
    calculado_en = Column(DateTime, default=datetime.utcnow,
                          comment="Última vez que se calculó este registro")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    empleado = relationship("Empleado", backref="saldos_mensuales")
