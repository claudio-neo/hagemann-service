"""
DATEV Service — Hagemann
Integración con DATEV Lohn & Gehalt via DATEVconnect API (OAuth 2.0).

En modo SANDBOX (DATEV_SANDBOX=true o config.activo sin credenciales) todas
las llamadas HTTP a DATEV se simulan localmente, devolviendo respuestas
ficticias. El CSV siempre funciona sin credenciales.

Documentación oficial (verificada 2026-05-29):
  - OIDC:        https://developer.datev.de/de/guides/authentication
  - Producto:    https://developer.datev.de/de/product-detail/hr-imports/2.0.0/overview
  - Bewegungsdaten (ASCII): https://apps.datev.de/help-center/documents/1007833
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

from ..models.datev import (
    DatevConfig, DatevExportLog,
    LOHNART_CONCEPTS, default_lohnart_mapping, default_phantomlohn,
)
from ..models.saldo_horas import SaldoHorasMensual
from ..models.empleado import Empleado, CentroCoste, Zeitgruppe
from ..models.vacaciones import SolicitudVacaciones, TipoAusencia, EstadoSolicitud

logger = logging.getLogger(__name__)

# ─── URLs DATEV (OpenID Connect real, verificado 2026-05-29) ─────────────────
# Producción: https://login.datev.de/openid/   ·   Sandbox: .../openidsandbox/
# Token endpoint vive en api.datev.de, no en login.datev.de.
def _oidc_base() -> str:
    """Base OIDC según modo: openidsandbox en sandbox, openid en producción."""
    return (
        "https://login.datev.de/openidsandbox"
        if _is_sandbox()
        else "https://login.datev.de/openid"
    )


DATEV_TOKEN_URL = "https://api.datev.de/token"
# Producto real para empujar Bewegungsdaten: Lohnimportdatenservice (hr:imports).
# El endpoint concreto se fija en Fase 3 al registrar la app del asesor.
DATEV_API_BASE = "https://api.datev.de"
DATEV_HR_IMPORTS_PRODUCT = "hr:imports"

# Scopes: dependen del producto registrado en developer.datev.de.
# 'hr:imports' como placeholder hasta confirmar con la app del asesor (Fase 3).
DATEV_SCOPES = "openid profile hr:imports"

# Timeout en segundos para llamadas HTTP
HTTP_TIMEOUT = 30


def _is_sandbox() -> bool:
    """Devuelve True si la variable de entorno DATEV_SANDBOX está activa."""
    return os.getenv("DATEV_SANDBOX", "true").lower() in ("true", "1", "yes")


# ─── Cifrado del client_secret en reposo ─────────────────────────────────────
# Clave Fernet en la env DATEV_SECRET_KEY. Si falta, se guarda en texto plano
# (modo dev) con aviso. Los valores cifrados llevan el prefijo "enc:".
_ENC_PREFIX = "enc:"


def _fernet():
    from cryptography.fernet import Fernet
    key = os.getenv("DATEV_SECRET_KEY")
    if not key:
        return None
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plain: Optional[str]) -> Optional[str]:
    """Cifra el client_secret antes de guardarlo. Sin clave → texto plano + aviso."""
    if not plain or plain.startswith(_ENC_PREFIX):
        return plain
    f = _fernet()
    if f is None:
        logger.warning("DATEV_SECRET_KEY no configurado — client_secret se guarda en texto plano")
        return plain
    return _ENC_PREFIX + f.encrypt(plain.encode()).decode()


def decrypt_secret(stored: Optional[str]) -> Optional[str]:
    """Descifra el client_secret almacenado. Texto plano → se devuelve tal cual."""
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored
    f = _fernet()
    if f is None:
        raise ValueError("client_secret cifrado pero falta DATEV_SECRET_KEY para descifrarlo")
    return f.decrypt(stored[len(_ENC_PREFIX):].encode()).decode()


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

    # Cifrar el client_secret antes de persistir
    if data.get("client_secret"):
        data = {**data, "client_secret": encrypt_secret(data["client_secret"])}

    # Campos actualizables
    allowed_fields = [
        "consultant_number", "client_number", "company_name",
        "fiscal_year_start", "client_id", "client_secret",
        "datev_guid", "payroll_type", "activo", "lohnart_mapping",
        "phantomlohn",
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
    return f"{_oidc_base()}/authorize?{urlencode(params)}"


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
        "client_secret": decrypt_secret(config.client_secret),
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
        "client_secret": decrypt_secret(config.client_secret),
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

    # ── Llamada real (Fase 3 — pendiente) ────────────────────────────────────
    # El envío directo por API se hará vía DATEV Lohnimportdatenservice
    # (producto hr:imports). El contrato concreto (endpoint, formato del cuerpo,
    # scopes) se fija al registrar la app del asesor en developer.datev.de.
    # Hasta entonces, el camino productivo es el fichero Bewegungsdaten
    # (export_bewegungsdaten_csv) importado con el ASCII-Import Assistent.
    raise NotImplementedError(
        "Envío directo a DATEV (Lohnimportdatenservice / hr:imports) pendiente "
        "de Fase 3: requiere la app DATEV del asesor (client_id/secret reales) y "
        "confirmar el contrato del producto. Use el export de Bewegungsdaten "
        "(POST /datev/export/bewegungsdaten) e impórtelo con el ASCII-Import Assistent."
    )


# ─────────────────────────────────────────────────────────────────────────────
#  EXPORTACIÓN BEWEGUNGSDATEN (ASCII-Import — DATEV Lohn und Gehalt)
# ─────────────────────────────────────────────────────────────────────────────
#
# Formato de movimientos para el ASCII-Import Assistent de DATEV Lohn und Gehalt
# (Erfassen > Bewegungsdaten > Importieren). Una fila por (empleado, Lohnart).
# El asistente DATEV permite mapear columnas y fijar separador, así que estas
# columnas son la referencia que el asesor configura una sola vez.
#
# ⚠️ Cada movimiento se importa contra una Lohnart/Lohnnummer definida por el
#    asesor para este Mandant (config.lohnart_mapping). Sin Lohnart → no se exporta.

BEWEGUNGSDATEN_HEADERS = [
    "Personalnummer",
    "Nachname",
    "Vorname",
    "Lohnart",
    "Wert",
    "Einheit",
    "Abrechnungsmonat",
    "Kostenstelle",
]


def get_lohnart_mapping(config: Optional[DatevConfig]) -> dict:
    """Mapeo Lohnart efectivo: el almacenado, completado con los conceptos por defecto."""
    base = default_lohnart_mapping()
    if config and config.lohnart_mapping:
        for concepto, valores in config.lohnart_mapping.items():
            if concepto in base and isinstance(valores, dict):
                base[concepto].update(valores)
    return base


def get_phantomlohn(config: Optional[DatevConfig]) -> dict:
    """Config Phantomlohn efectiva: la almacenada, completada con los defaults."""
    base = default_phantomlohn()
    if config and config.phantomlohn and isinstance(config.phantomlohn, dict):
        base.update(config.phantomlohn)
    return base


def _schicht_zeitgruppe_ids(db: Session) -> set:
    """IDs de Zeitgruppen de tipo SCHICHT (turnos BMB Früh/Spät/Nacht → Phantomlohn)."""
    rows = db.query(Zeitgruppe.id).filter(Zeitgruppe.tipo == "SCHICHT").all()
    return {r[0] for r in rows}


def _concept_values(saldo, sick_days: int, vacation_days: int) -> dict:
    """Valor numérico de cada concepto exportable para un empleado/mes."""
    horas_reales = float(saldo.horas_reales or 0)
    horas_planificadas = float(saldo.horas_planificadas or 0)
    return {
        "normalstunden": round(horas_reales, 2),
        "ueberstunden": max(0.0, round(horas_reales - horas_planificadas, 2)),
        "krankheit": sick_days,
        "urlaub": vacation_days,
        "saldo": round(float(saldo.saldo_final or 0), 2),
    }


def build_bewegungsdaten_rows(
    db: Session,
    year: int,
    month: int,
    config: Optional[DatevConfig],
) -> list[dict]:
    """
    Construye las filas de Bewegungsdaten del mes.

    Una fila por cada (empleado, concepto) cuyo concepto esté activo, tenga
    Lohnart asignada y valor distinto de cero.
    """
    abrechnungsmonat = f"{month:02d}/{year}"
    mapping = get_lohnart_mapping(config)
    phantom = get_phantomlohn(config)
    schicht_ids = _schicht_zeitgruppe_ids(db) if phantom.get("aktiv") else set()

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

    # Conceptos que el Phantomlohn sustituye por una doble Lohnart para Schicht-MA
    PHANTOM_CONCEPTS = {"krankheit", "urlaub"}

    out: list[dict] = []
    for saldo, emp in rows:
        sick_days = _count_ausencia(db, emp.id, year, month, TipoAusencia.BAJA_MEDICA)
        vacation_days = _count_ausencia(db, emp.id, year, month, TipoAusencia.VACACIONES)
        kostenstelle = _get_kostenstelle(db, emp.id)
        valores = _concept_values(saldo, sick_days, vacation_days)
        es_phantom = emp.zeitgruppe_id in schicht_ids

        def _emit(lohnart, wert, einheit):
            lohnart = (lohnart or "").strip()
            if not lohnart or not wert:
                return
            out.append({
                "Personalnummer": str(emp.id_nummer or ""),
                "Nachname": emp.apellido or "",
                "Vorname": emp.nombre or "",
                "Lohnart": lohnart,
                "Wert": wert,
                "Einheit": einheit,
                "Abrechnungsmonat": abrechnungsmonat,
                "Kostenstelle": kostenstelle,
            })

        for concepto in LOHNART_CONCEPTS:
            wert = valores.get(concepto, 0)
            # Phantomlohn (BMB Schicht): Krankheit/Urlaub → doble Lohnart (Phantom '+' e IST '−')
            if es_phantom and concepto in PHANTOM_CONCEPTS:
                _emit(phantom.get(f"{concepto}_phantom"), wert, phantom.get("einheit", "Tage"))
                _emit(phantom.get(f"{concepto}_ist"), wert, phantom.get("einheit", "Tage"))
                continue
            # Mapeo global normal
            cfg = mapping.get(concepto, {})
            if not cfg.get("aktiv"):
                continue
            _emit(cfg.get("lohnart"), wert, cfg.get("einheit", ""))
    return out


def export_bewegungsdaten_csv(
    db: Session,
    year: int,
    month: int,
    delimiter: str = ";",
    decimal_sep: str = ",",
    include_header: bool = True,
) -> bytes:
    """
    Genera el fichero CSV de Bewegungsdaten para el ASCII-Import de DATEV
    Lohn und Gehalt. No requiere credenciales.

    Defaults según convención alemana DATEV: separador ';', decimal ',',
    cabecera incluida, CRLF y UTF-8-BOM (necesario para Ü/ö/ß).

    Returns:
        bytes del CSV codificado en UTF-8-BOM.
    """
    config = get_config(db)
    data_rows = build_bewegungsdaten_rows(db, year, month, config)

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=BEWEGUNGSDATEN_HEADERS,
        delimiter=delimiter,
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    if include_header:
        writer.writeheader()

    for row in data_rows:
        wert = str(row["Wert"])
        if decimal_sep != ".":
            wert = wert.replace(".", decimal_sep)
        writer.writerow({**row, "Wert": wert})

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
