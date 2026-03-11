"""
DATEV Service — Hagemann
Integración con DATEV Lohn & Gehalt via DATEVconnect API (OAuth 2.0).

En modo SANDBOX (DATEV_SANDBOX=true o config.activo sin credenciales) todas
las llamadas HTTP a DATEV se simulan localmente, devolviendo respuestas
ficticias. El CSV siempre funciona sin credenciales.

Documentación oficial:
  https://developer.datev.de/datev/platform/de/dtvf/lohn-und-gehalt
  https://apps.datev.de/help-center/documents/1080181
"""
import csv
import io
import os
import uuid
import logging
from datetime import datetime, date, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from ..models.datev import DatevConfig, DatevExportLog
from ..models.saldo_horas import SaldoHorasMensual
from ..models.empleado import Empleado, CentroCoste
from ..models.vacaciones import SolicitudVacaciones, TipoAusencia, EstadoSolicitud

logger = logging.getLogger(__name__)

# ─── URLs DATEV DATEVconnect ─────────────────────────────────────────────────
DATEV_AUTH_BASE = "https://login.datev.de/openiddict"
DATEV_AUTH_URL = f"{DATEV_AUTH_BASE}/authorize"
DATEV_TOKEN_URL = f"{DATEV_AUTH_BASE}/token"
DATEV_API_BASE = "https://api.datev.de/marketplace"
DATEV_PAYROLL_ENDPOINT = f"{DATEV_API_BASE}/v1/payroll/lohngehalt"

# Scopes requeridos para exportación de nómina
DATEV_SCOPES = "openid profile datev:payroll:read datev:payroll:write"

# Timeout en segundos para llamadas HTTP
HTTP_TIMEOUT = 30


def _is_sandbox() -> bool:
    """Devuelve True si la variable de entorno DATEV_SANDBOX está activa."""
    return os.getenv("DATEV_SANDBOX", "true").lower() in ("true", "1", "yes")


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

def get_config(db: Session) -> Optional[DatevConfig]:
    """
    Obtiene la configuración DATEV activa.
    Devuelve None si no hay configuración registrada.
    """
    return (
        db.query(DatevConfig)
        .filter(DatevConfig.activo == True)
        .first()
    )


def upsert_config(db: Session, data: dict) -> DatevConfig:
    """
    Crea o actualiza la configuración DATEV activa.
    Si ya existe un registro activo, lo actualiza. Si no, lo crea.
    """
    config = get_config(db)
    if config is None:
        config = DatevConfig()
        db.add(config)

    # Campos actualizables
    allowed_fields = [
        "consultant_number", "client_number", "company_name",
        "fiscal_year_start", "client_id", "client_secret",
        "datev_guid", "payroll_type", "activo",
    ]
    for field in allowed_fields:
        if field in data:
            setattr(config, field, data[field])

    config.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(config)
    return config


# ─────────────────────────────────────────────────────────────────────────────
#  OAUTH 2.0
# ─────────────────────────────────────────────────────────────────────────────

def generate_oauth_url(config: DatevConfig, redirect_uri: str, state: str = None) -> str:
    """
    Genera la URL de autorización OAuth 2.0 de DATEV.

    El usuario debe visitar esta URL en su navegador para conceder acceso.
    DATEV redirigirá a redirect_uri con ?code=XXX&state=YYY.

    Args:
        config: Configuración DATEV activa
        redirect_uri: URI de callback registrada en la App DATEV
        state: Valor opaco para protección CSRF (generado automáticamente si None)

    Returns:
        URL completa de autorización
    """
    if not state:
        state = str(uuid.uuid4())

    params = {
        "response_type": "code",
        "client_id": config.client_id or "DATEV_CLIENT_ID_PENDIENTE",
        "redirect_uri": redirect_uri,
        "scope": DATEV_SCOPES,
        "state": state,
        "nonce": str(uuid.uuid4()),
    }
    return f"{DATEV_AUTH_URL}?{urlencode(params)}"


def exchange_code(
    config: DatevConfig,
    code: str,
    redirect_uri: str,
    db: Session,
) -> dict:
    """
    Intercambia el código de autorización OAuth por tokens de acceso.

    En modo sandbox devuelve tokens ficticios sin hacer llamada HTTP real.

    Args:
        config: Configuración DATEV
        code: Código de autorización recibido en el callback
        redirect_uri: Mismo redirect_uri usado en generate_oauth_url
        db: Sesión de base de datos para guardar tokens

    Returns:
        dict con access_token, refresh_token, expires_in, scope
    """
    if _is_sandbox():
        logger.info("[DATEV SANDBOX] exchange_code simulado")
        fake_expiry = datetime.utcnow() + timedelta(hours=1)
        _save_tokens(
            config, db,
            access_token=f"sandbox_access_{uuid.uuid4().hex}",
            refresh_token=f"sandbox_refresh_{uuid.uuid4().hex}",
            expires_at=fake_expiry,
            scope=DATEV_SCOPES,
        )
        return {
            "access_token": config.access_token,
            "refresh_token": config.refresh_token,
            "expires_in": 3600,
            "scope": DATEV_SCOPES,
            "sandbox": True,
        }

    # ── Llamada real ─────────────────────────────────────────────────────────
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        resp = client.post(DATEV_TOKEN_URL, data=payload)
        resp.raise_for_status()
        token_data = resp.json()

    expires_at = datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
    _save_tokens(
        config, db,
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_at=expires_at,
        scope=token_data.get("scope"),
    )
    return token_data


def refresh_access_token(config: DatevConfig, db: Session) -> None:
    """
    Renueva el access_token usando el refresh_token almacenado.

    En modo sandbox regenera tokens ficticios.
    Actualiza config en base de datos.
    """
    if _is_sandbox():
        logger.info("[DATEV SANDBOX] refresh_token simulado")
        _save_tokens(
            config, db,
            access_token=f"sandbox_access_{uuid.uuid4().hex}",
            refresh_token=config.refresh_token,  # El refresh token no cambia en sandbox
            expires_at=datetime.utcnow() + timedelta(hours=1),
            scope=config.token_scope or DATEV_SCOPES,
        )
        return

    if not config.refresh_token:
        raise ValueError("No hay refresh_token almacenado. Debe autorizar de nuevo via OAuth.")

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": config.refresh_token,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        resp = client.post(DATEV_TOKEN_URL, data=payload)
        resp.raise_for_status()
        token_data = resp.json()

    expires_at = datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
    _save_tokens(
        config, db,
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token", config.refresh_token),
        expires_at=expires_at,
        scope=token_data.get("scope", config.token_scope),
    )


def _save_tokens(
    config: DatevConfig,
    db: Session,
    access_token: str,
    refresh_token: Optional[str],
    expires_at: datetime,
    scope: Optional[str],
) -> None:
    """Persiste los tokens en base de datos."""
    config.access_token = access_token
    config.refresh_token = refresh_token
    config.token_expires_at = expires_at
    config.token_scope = scope
    config.updated_at = datetime.utcnow()
    db.commit()


def _ensure_valid_token(config: DatevConfig, db: Session) -> None:
    """
    Verifica que el access_token existe y no ha expirado.
    Si está a punto de expirar (< 5 min), lo renueva automáticamente.
    """
    if _is_sandbox():
        return  # En sandbox no necesitamos tokens reales

    if not config.access_token:
        raise ValueError(
            "No hay access_token. Debe autorizar la conexión DATEV via OAuth."
        )

    if config.token_expires_at:
        margin = timedelta(minutes=5)
        if datetime.utcnow() + margin >= config.token_expires_at:
            logger.info("Token DATEV próximo a expirar. Renovando...")
            refresh_access_token(config, db)


# ─────────────────────────────────────────────────────────────────────────────
#  CONSTRUCCIÓN DEL PAYLOAD
# ─────────────────────────────────────────────────────────────────────────────

def _count_ausencia(
    db: Session,
    empleado_id,
    year: int,
    month: int,
    tipo: str,
) -> int:
    """Cuenta días de ausencia aprobados de un tipo dado en el mes."""
    mes_inicio = date(year, month, 1)
    mes_fin = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    from sqlalchemy import func
    result = (
        db.query(func.sum(SolicitudVacaciones.dias))
        .filter(
            SolicitudVacaciones.empleado_id == empleado_id,
            SolicitudVacaciones.estado == EstadoSolicitud.APROBADA,
            SolicitudVacaciones.tipo_ausencia == tipo,
            SolicitudVacaciones.fecha_inicio >= mes_inicio,
            SolicitudVacaciones.fecha_inicio < mes_fin,
        )
        .scalar()
    )
    return int(result or 0)


def _get_kostenstelle(db: Session, empleado_id) -> str:
    """
    Obtiene el código del centro de coste principal del empleado.
    Usa el centro de coste con más minutos acumulados en SegmentoTiempo.
    Devuelve '0000' si no tiene asignación registrada.
    """
    from sqlalchemy import func
    from ..models.fichaje import SegmentoTiempo
    from ..models.empleado import CentroCoste

    result = (
        db.query(
            CentroCoste.codigo,
            func.sum(SegmentoTiempo.minutos).label("total_minutos"),
        )
        .join(SegmentoTiempo, SegmentoTiempo.centro_coste_id == CentroCoste.id)
        .filter(SegmentoTiempo.empleado_id == empleado_id)
        .group_by(CentroCoste.codigo)
        .order_by(func.sum(SegmentoTiempo.minutos).desc())
        .first()
    )
    return result.codigo if result else "0000"


def build_export_payload(
    db: Session,
    year: int,
    month: int,
    config: DatevConfig,
) -> dict:
    """
    Construye el payload JSON en formato DATEV Lohn & Gehalt.

    Consulta saldos_horas_mensuales del mes y mapea al formato DATEV:
      - PersonalnummerArbeitnehmer = empleado.id_nummer
      - NachnameMitarbeiter = empleado.apellido
      - VornameMitarbeiter = empleado.nombre
      - Abrechnungszeitraum = YYYYMM
      - Normalstunden = horas_reales (horas efectivamente trabajadas)
      - Überstunden = overtime_hours (horas extra aprobadas)
      - Krankheitstage = días de baja médica en el mes
      - Urlaubstage = días de vacaciones en el mes
      - Zeitkonto_Saldo = saldo_final del mes
      - Kostenstelle = código del centro de coste principal

    Returns:
        dict con BeraternummerDatev, MandantennummerDatev, Abrechnungszeitraum,
        Arbeitnehmer (lista), y metadata
    """
    abrechnungszeitraum = f"{year}{month:02d}"

    rows = (
        db.query(SaldoHorasMensual, Empleado)
        .join(Empleado, SaldoHorasMensual.empleado_id == Empleado.id)
        .filter(
            SaldoHorasMensual.anio == year,
            SaldoHorasMensual.mes == month,
            Empleado.activo == True,
        )
        .order_by(Empleado.id_nummer)
        .all()
    )

    arbeitnehmer_list = []
    for saldo, emp in rows:
        horas_reales = float(saldo.horas_reales or 0)
        # Horas extra = diferencia positiva entre real y planificado
        horas_planificadas = float(saldo.horas_planificadas or 0)
        ueberstunden = max(0.0, round(horas_reales - horas_planificadas, 2))

        sick_days = _count_ausencia(
            db, emp.id, year, month, TipoAusencia.BAJA_MEDICA
        )
        vacation_days = _count_ausencia(
            db, emp.id, year, month, TipoAusencia.VACACIONES
        )
        kostenstelle = _get_kostenstelle(db, emp.id)

        arbeitnehmer_list.append({
            "PersonalnummerArbeitnehmer": str(emp.id_nummer or ""),
            "NachnameMitarbeiter": emp.apellido or "",
            "VornameMitarbeiter": emp.nombre or "",
            "Abrechnungszeitraum": abrechnungszeitraum,
            "Normalstunden": round(horas_reales, 2),
            "Überstunden": ueberstunden,
            "Krankheitstage": sick_days,
            "Urlaubstage": vacation_days,
            "Zeitkonto_Saldo": round(float(saldo.saldo_final or 0), 2),
            "Kostenstelle": kostenstelle,
        })

    return {
        "BeraternummerDatev": config.consultant_number,
        "MandantennummerDatev": config.client_number,
        "Unternehmensname": config.company_name,
        "Abrechnungszeitraum": abrechnungszeitraum,
        "Arbeitnehmer": arbeitnehmer_list,
        "metadata": {
            "total_empleados": len(arbeitnehmer_list),
            "generado_en": datetime.utcnow().isoformat(),
            "payroll_type": config.payroll_type,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  ENVÍO A DATEV
# ─────────────────────────────────────────────────────────────────────────────

def send_to_datev(
    config: DatevConfig,
    payload: dict,
    db: Session,
) -> dict:
    """
    Envía el payload a la API DATEV Lohn & Gehalt.

    En modo SANDBOX (DATEV_SANDBOX=true o sin credenciales reales):
      - No realiza ninguna llamada HTTP
      - Devuelve respuesta simulada exitosa con import_id ficticio
      - El log indica claramente "SANDBOX MODE"

    Returns:
        dict con status, import_id, message, sandbox (bool)
    """
    sandbox_mode = _is_sandbox() or not config.client_id or not config.access_token

    if sandbox_mode:
        import_id = f"SANDBOX-{uuid.uuid4().hex[:12].upper()}"
        logger.info(f"[DATEV SANDBOX] Exportación simulada. import_id={import_id}")
        return {
            "status": "success",
            "import_id": import_id,
            "message": "SANDBOX MODE — exportación simulada correctamente",
            "records_accepted": len(payload.get("Arbeitnehmer", [])),
            "sandbox": True,
            "datev_response_code": "200",
        }

    # ── Llamada real ─────────────────────────────────────────────────────────
    _ensure_valid_token(config, db)

    headers = {
        "Authorization": f"Bearer {config.access_token}",
        "Content-Type": "application/json",
        "X-DATEV-Client-Id": config.client_id,
    }
    if config.datev_guid:
        headers["X-DATEV-Mandant"] = config.datev_guid

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(
                DATEV_PAYROLL_ENDPOINT,
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"[DATEV] Error HTTP {e.response.status_code}: {e.response.text}")
        raise

    return {
        "status": "success",
        "import_id": data.get("importId") or data.get("id"),
        "message": data.get("message", "Importado correctamente"),
        "records_accepted": data.get("recordsAccepted", 0),
        "sandbox": False,
        "datev_response_code": str(resp.status_code),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  EXPORTACIÓN CSV (alternativa offline)
# ─────────────────────────────────────────────────────────────────────────────

# Cabeceras del CSV en formato DATEV Lohn & Gehalt (DTVF compatible)
CSV_HEADERS = [
    "PersonalnummerArbeitnehmer",
    "NachnameMitarbeiter",
    "VornameMitarbeiter",
    "Abrechnungszeitraum",
    "Normalstunden",
    "Überstunden",
    "Krankheitstage",
    "Urlaubstage",
    "Zeitkonto_Saldo",
    "Kostenstelle",
    "BeraternummerDatev",
    "MandantennummerDatev",
]


def export_to_csv(
    db: Session,
    year: int,
    month: int,
) -> bytes:
    """
    Genera el CSV de exportación DATEV Lohn & Gehalt como alternativa offline.

    No requiere credenciales OAuth. Útil para importar manualmente en DATEV.
    El formato es compatible con la importación DTVF (DATEV-Format).

    Returns:
        bytes del CSV codificado en UTF-8-BOM (requerido por DATEV)
    """
    config = get_config(db)

    consultant_number = config.consultant_number if config else "0000000000"
    client_number = config.client_number if config else "00000"

    # Construir payload para reutilizar la lógica de mapeo
    if config:
        payload = build_export_payload(db, year, month, config)
    else:
        # Sin config: datos mínimos para el CSV
        abrechnungszeitraum = f"{year}{month:02d}"
        rows = (
            db.query(SaldoHorasMensual, Empleado)
            .join(Empleado, SaldoHorasMensual.empleado_id == Empleado.id)
            .filter(
                SaldoHorasMensual.anio == year,
                SaldoHorasMensual.mes == month,
                Empleado.activo == True,
            )
            .order_by(Empleado.id_nummer)
            .all()
        )
        arbeitnehmer_list = []
        for saldo, emp in rows:
            horas_reales = float(saldo.horas_reales or 0)
            horas_planificadas = float(saldo.horas_planificadas or 0)
            sick_days = _count_ausencia(db, emp.id, year, month, TipoAusencia.BAJA_MEDICA)
            vacation_days = _count_ausencia(db, emp.id, year, month, TipoAusencia.VACACIONES)
            arbeitnehmer_list.append({
                "PersonalnummerArbeitnehmer": str(emp.id_nummer or ""),
                "NachnameMitarbeiter": emp.apellido or "",
                "VornameMitarbeiter": emp.nombre or "",
                "Abrechnungszeitraum": abrechnungszeitraum,
                "Normalstunden": round(horas_reales, 2),
                "Überstunden": max(0.0, round(horas_reales - horas_planificadas, 2)),
                "Krankheitstage": sick_days,
                "Urlaubstage": vacation_days,
                "Zeitkonto_Saldo": round(float(saldo.saldo_final or 0), 2),
                "Kostenstelle": _get_kostenstelle(db, emp.id),
            })
        payload = {
            "BeraternummerDatev": consultant_number,
            "MandantennummerDatev": client_number,
            "Arbeitnehmer": arbeitnehmer_list,
        }

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=CSV_HEADERS,
        delimiter=";",    # DATEV usa punto y coma
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",  # CRLF requerido por DATEV
    )
    writer.writeheader()

    for row in payload["Arbeitnehmer"]:
        writer.writerow({
            "PersonalnummerArbeitnehmer": row["PersonalnummerArbeitnehmer"],
            "NachnameMitarbeiter":        row["NachnameMitarbeiter"],
            "VornameMitarbeiter":         row["VornameMitarbeiter"],
            "Abrechnungszeitraum":        row["Abrechnungszeitraum"],
            "Normalstunden":              str(row["Normalstunden"]).replace(".", ","),
            "Überstunden":                str(row["Überstunden"]).replace(".", ","),
            "Krankheitstage":             row["Krankheitstage"],
            "Urlaubstage":                row["Urlaubstage"],
            "Zeitkonto_Saldo":            str(row["Zeitkonto_Saldo"]).replace(".", ","),
            "Kostenstelle":               row["Kostenstelle"],
            "BeraternummerDatev":         payload["BeraternummerDatev"],
            "MandantennummerDatev":       payload["MandantennummerDatev"],
        })

    # UTF-8-BOM requerido por DATEV para caracteres alemanes (Ü, ö, etc.)
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
#  LOG DE EXPORTACIÓN
# ─────────────────────────────────────────────────────────────────────────────

def save_export_log(
    db: Session,
    year: int,
    month: int,
    exported_by: str,
    status: str,
    records_sent: int,
    response_code: Optional[str] = None,
    response_body: Optional[str] = None,
    error_message: Optional[str] = None,
    file_path: Optional[str] = None,
) -> DatevExportLog:
    """Guarda un registro en el log de exportaciones."""
    log = DatevExportLog(
        year=year,
        month=month,
        exported_by=exported_by,
        exported_at=datetime.utcnow(),
        status=status,
        records_sent=records_sent,
        response_code=response_code,
        response_body=response_body,
        error_message=error_message,
        file_path=file_path,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_export_history(
    db: Session,
    limit: int = 50,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> list:
    """Obtiene el historial de exportaciones, ordenado por fecha descendente."""
    query = db.query(DatevExportLog)
    if year is not None:
        query = query.filter(DatevExportLog.year == year)
    if month is not None:
        query = query.filter(DatevExportLog.month == month)
    return query.order_by(DatevExportLog.exported_at.desc()).limit(limit).all()
