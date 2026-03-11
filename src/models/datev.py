"""
Modelos DATEV — Hagemann
Almacena configuración OAuth y log de exportaciones a DATEV Lohn & Gehalt.
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, Integer, Boolean, DateTime, Date, String, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from ..database import Base


class DatevConfig(Base):
    """
    Configuración OAuth 2.0 y parámetros de conexión con DATEV.

    Un único registro activo (activo=True) por instancia.
    """

    __tablename__ = "datev_config"
    __table_args__ = {"schema": "hagemann"}

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        comment="PK UUID",
    )

    # ── Identificación DATEV ────────────────────────────────────────────────
    consultant_number = Column(
        String(20), nullable=False,
        comment="Beraternummer DATEV (10 dígitos asignados al asesor fiscal)",
    )
    client_number = Column(
        String(20), nullable=False,
        comment="Mandantennummer (número de mandante/empresa en DATEV, 1-99999)",
    )
    company_name = Column(
        String(200), nullable=False,
        comment="Unternehmensname — razón social de la empresa",
    )
    fiscal_year_start = Column(
        Date, nullable=False,
        comment="Inicio del ejercicio fiscal (Wirtschaftsjahrbeginn)",
    )

    # ── OAuth 2.0 — DATEVconnect ────────────────────────────────────────────
    client_id = Column(
        String(200), nullable=True,
        comment="DATEV OAuth App Client ID (del portal developer.datev.de)",
    )
    client_secret = Column(
        String(500), nullable=True,
        comment="DATEV OAuth App Client Secret (almacenar encriptado en producción)",
    )
    access_token = Column(
        Text, nullable=True,
        comment="Token de acceso OAuth actual (JWT corto plazo, ~1h)",
    )
    refresh_token = Column(
        Text, nullable=True,
        comment="Refresh token OAuth (largo plazo, para renovar access_token)",
    )
    token_expires_at = Column(
        DateTime, nullable=True,
        comment="Timestamp de expiración del access_token",
    )
    token_scope = Column(
        String(500), nullable=True,
        comment="Scopes OAuth concedidos (ej: datev:payroll:read datev:payroll:write)",
    )

    # ── Configuración específica DATEV ───────────────────────────────────────
    datev_guid = Column(
        String(100), nullable=True,
        comment="GUID de la empresa en DATEV (devuelto tras primer acceso a API)",
    )
    payroll_type = Column(
        String(50), nullable=False, default="Lohn",
        comment="Tipo de nómina: 'Lohn' (por horas) o 'Gehalt' (mensual fijo)",
    )

    # ── Control ──────────────────────────────────────────────────────────────
    activo = Column(
        Boolean, nullable=False, default=True,
        comment="Solo debe haber un registro activo",
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<DatevConfig consultant={self.consultant_number} "
            f"client={self.client_number} activo={self.activo}>"
        )


class DatevExportLog(Base):
    """
    Historial de exportaciones enviadas (o simuladas) a DATEV.

    Cada llamada a POST /datev/export con dry_run=False genera un registro aquí.
    """

    __tablename__ = "datev_export_log"
    __table_args__ = {"schema": "hagemann"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Periodo exportado ───────────────────────────────────────────────────
    year = Column(Integer, nullable=False, comment="Año del periodo exportado")
    month = Column(Integer, nullable=False, comment="Mes del periodo exportado (1-12)")

    # ── Auditoría ───────────────────────────────────────────────────────────
    exported_by = Column(
        String(100), nullable=False,
        comment="Nombre/nick del usuario que inició la exportación",
    )
    exported_at = Column(
        DateTime, nullable=False, default=datetime.utcnow,
        comment="Timestamp de la exportación",
    )

    # ── Resultado ───────────────────────────────────────────────────────────
    status = Column(
        String(20), nullable=False,
        comment="Resultado: 'success', 'error' o 'partial'",
    )
    records_sent = Column(
        Integer, nullable=False, default=0,
        comment="Número de registros de empleado enviados",
    )
    response_code = Column(
        String(10), nullable=True,
        comment="Código HTTP de respuesta de DATEV (ej: '200', '400', '500')",
    )
    response_body = Column(
        Text, nullable=True,
        comment="Cuerpo de la respuesta DATEV (JSON truncado si muy largo)",
    )
    error_message = Column(
        Text, nullable=True,
        comment="Mensaje de error legible (en caso de fallo)",
    )
    file_path = Column(
        String(500), nullable=True,
        comment="Ruta del archivo CSV/backup generado localmente",
    )

    # ── Timestamps ──────────────────────────────────────────────────────────
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<DatevExportLog {self.year}-{self.month:02d} "
            f"status={self.status} records={self.records_sent}>"
        )
