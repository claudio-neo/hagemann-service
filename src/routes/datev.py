"""
DATEV Routes — Hagemann
Endpoints para gestión de configuración DATEV, OAuth 2.0 y exportaciones.

Prefix: /api/v1/datev
Tags: DATEV
"""
import io
import json
import os
from datetime import datetime, date
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import datev_service

router = APIRouter(prefix="/datev", tags=["DATEV"])


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMAS
# ─────────────────────────────────────────────────────────────────────────────

class DatevConfigIn(BaseModel):
    """Body para crear/actualizar la configuración DATEV."""
    consultant_number: str = Field(
        ..., max_length=20,
        description="Beraternummer DATEV (10 dígitos asignados al asesor fiscal)",
        example="1234567890",
    )
    client_number: str = Field(
        ..., max_length=20,
        description="Mandantennummer (número de mandante en DATEV, 1-99999)",
        example="12345",
    )
    company_name: str = Field(
        ..., max_length=200,
        description="Unternehmensname (razón social de la empresa)",
        example="Hagemann GmbH",
    )
    fiscal_year_start: date = Field(
        ...,
        description="Inicio del ejercicio fiscal (Wirtschaftsjahrbeginn)",
        example="2026-01-01",
    )
    client_id: Optional[str] = Field(
        None, max_length=200,
        description="DATEV OAuth App Client ID (portal developer.datev.de)",
    )
    client_secret: Optional[str] = Field(
        None, max_length=500,
        description="DATEV OAuth App Client Secret",
    )
    datev_guid: Optional[str] = Field(
        None, max_length=100,
        description="GUID de la empresa en DATEV",
    )
    payroll_type: str = Field(
        "Lohn", max_length=50,
        description="Tipo de nómina: 'Lohn' (por horas) o 'Gehalt' (mensual fijo)",
    )


class DatevConfigOut(BaseModel):
    """Respuesta de configuración DATEV (sin client_secret)."""
    id: UUID
    consultant_number: str
    client_number: str
    company_name: str
    fiscal_year_start: date
    client_id: Optional[str]
    # client_secret OCULTO intencionalmente
    datev_guid: Optional[str]
    payroll_type: str
    activo: bool
    token_expires_at: Optional[datetime]
    token_scope: Optional[str]
    has_access_token: bool
    has_refresh_token: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ExportRequest(BaseModel):
    """Body para la exportación DATEV."""
    year: int = Field(..., ge=2020, le=2099, example=2026)
    month: int = Field(..., ge=1, le=12, example=3)
    dry_run: bool = Field(
        True,
        description=(
            "Si True → devuelve el payload JSON sin enviar ni guardar log. "
            "Si False → envía a DATEV (o sandbox) y guarda el log."
        ),
    )
    exported_by: str = Field(
        "api", max_length=100,
        description="Nombre/nick del usuario que inicia la exportación",
    )


class CsvRequest(BaseModel):
    """Body para exportación CSV alternativa."""
    year: int = Field(..., ge=2020, le=2099, example=2026)
    month: int = Field(..., ge=1, le=12, example=3)


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/config",
    response_model=DatevConfigOut,
    summary="Ver configuración DATEV actual",
    description=(
        "Devuelve la configuración DATEV activa. "
        "El campo `client_secret` nunca se devuelve por seguridad. "
        "`has_access_token` y `has_refresh_token` indican si hay tokens almacenados."
    ),
)
def get_config(db: Session = Depends(get_db)):
    config = datev_service.get_config(db)
    if config is None:
        raise HTTPException(
            status_code=404,
            detail="Keine DATEV-Konfiguration vorhanden. Verwenden Sie POST /datev/config.",
        )
    return DatevConfigOut(
        id=config.id,
        consultant_number=config.consultant_number,
        client_number=config.client_number,
        company_name=config.company_name,
        fiscal_year_start=config.fiscal_year_start,
        client_id=config.client_id,
        datev_guid=config.datev_guid,
        payroll_type=config.payroll_type,
        activo=config.activo,
        token_expires_at=config.token_expires_at,
        token_scope=config.token_scope,
        has_access_token=bool(config.access_token),
        has_refresh_token=bool(config.refresh_token),
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.post(
    "/config",
    response_model=DatevConfigOut,
    summary="Crear/actualizar configuración DATEV",
    description=(
        "Crea o actualiza la configuración DATEV. "
        "Solo puede existir un registro activo. "
        "Si existiert bereits, lo actualiza en lugar de crear uno nuevo."
    ),
)
def upsert_config(body: DatevConfigIn, db: Session = Depends(get_db)):
    config = datev_service.upsert_config(db, body.model_dump())
    return DatevConfigOut(
        id=config.id,
        consultant_number=config.consultant_number,
        client_number=config.client_number,
        company_name=config.company_name,
        fiscal_year_start=config.fiscal_year_start,
        client_id=config.client_id,
        datev_guid=config.datev_guid,
        payroll_type=config.payroll_type,
        activo=config.activo,
        token_expires_at=config.token_expires_at,
        token_scope=config.token_scope,
        has_access_token=bool(config.access_token),
        has_refresh_token=bool(config.refresh_token),
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  OAUTH 2.0
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/oauth/authorize",
    summary="Obtener URL de autorización DATEV OAuth",
    description=(
        "Devuelve la URL de autorización OAuth 2.0 de DATEV. "
        "El administrador debe visitar esta URL para conceder acceso a la aplicación. "
        "Después DATEV redirige a /datev/oauth/callback con el código de autorización."
    ),
)
def oauth_authorize(
    request: Request,
    db: Session = Depends(get_db),
):
    config = datev_service.get_config(db)
    if config is None:
        raise HTTPException(
            404,
            "Configure primero la integración DATEV via POST /datev/config",
        )
    if not config.client_id:
        raise HTTPException(
            422,
            "client_id no configurado. Añada las credenciales OAuth via POST /datev/config",
        )

    # Construir redirect_uri dinámicamente desde la request
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/v1/datev/oauth/callback"

    auth_url = datev_service.generate_oauth_url(config, redirect_uri)
    return {
        "authorization_url": auth_url,
        "redirect_uri": redirect_uri,
        "instructions": (
            "Visita la authorization_url en tu navegador para autorizar la conexión DATEV. "
            "Después del login DATEV serás redirigido de vuelta automáticamente."
        ),
    }


@router.get(
    "/oauth/callback",
    summary="Callback OAuth DATEV — intercambiar código por tokens",
    description=(
        "DATEV redirige aquí tras la autorización con ?code=XXX&state=YYY. "
        "Intercambia el código por tokens de acceso y los almacena."
    ),
)
def oauth_callback(
    code: str = Query(..., description="Código de autorización OAuth"),
    state: Optional[str] = Query(None, description="Estado CSRF"),
    db: Session = Depends(get_db),
):
    config = datev_service.get_config(db)
    if config is None:
        raise HTTPException(404, "DATEV-Konfiguration nicht gefunden")

    # Reconstruir redirect_uri — debe ser igual al usado en authorize
    # En producción: usar el mismo base_url
    redirect_uri = os.getenv(
        "DATEV_REDIRECT_URI",
        "http://localhost:8013/api/v1/datev/oauth/callback",
    )

    try:
        token_data = datev_service.exchange_code(config, code, redirect_uri, db)
    except Exception as e:
        raise HTTPException(500, f"Fehler beim OAuth-Code-Austausch: {str(e)}")

    return {
        "status": "ok",
        "message": "Autorización DATEV completada correctamente",
        "expires_in": token_data.get("expires_in"),
        "scope": token_data.get("scope"),
        "sandbox": token_data.get("sandbox", False),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  EXPORTACIÓN
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/export",
    summary="Exportar datos de nómina a DATEV",
    description="""
Exporta los datos de horas del mes especificado a DATEV Lohn & Gehalt.

**dry_run=true** (por defecto):
- Devuelve el payload JSON que se enviaría a DATEV
- No envía nada, no guarda log
- Útil para revisar los datos antes de enviar

**dry_run=false**:
- Envía el payload a DATEV (o simula en modo sandbox)
- Guarda un registro en el historial de exportaciones
- En modo sandbox devuelve un import_id ficticio
""",
)
def export_to_datev(body: ExportRequest, db: Session = Depends(get_db)):
    config = datev_service.get_config(db)
    if config is None:
        raise HTTPException(
            404,
            "No hay configuración DATEV. Configure primero via POST /datev/config",
        )

    # Construir payload
    try:
        payload = datev_service.build_export_payload(db, body.year, body.month, config)
    except Exception as e:
        raise HTTPException(500, f"Fehler beim Aufbau der Nutzdaten: {str(e)}")

    if body.dry_run:
        return {
            "dry_run": True,
            "message": "Preview del payload — no se ha enviado nada",
            "payload": payload,
        }

    # Envío real (o sandbox)
    sandbox_mode = datev_service._is_sandbox() or not config.client_id

    try:
        result = datev_service.send_to_datev(config, payload, db)
    except Exception as e:
        # Guardar log de error
        datev_service.save_export_log(
            db,
            year=body.year,
            month=body.month,
            exported_by=body.exported_by,
            status="error",
            records_sent=0,
            error_message=str(e),
        )
        raise HTTPException(500, f"Fehler beim Senden an DATEV: {str(e)}")

    # Guardar log de éxito
    response_body = json.dumps(result)
    if sandbox_mode:
        response_body = json.dumps({**result, "_nota": "SANDBOX MODE — no se enviaron datos reales"})

    log = datev_service.save_export_log(
        db,
        year=body.year,
        month=body.month,
        exported_by=body.exported_by,
        status="success",
        records_sent=len(payload.get("Arbeitnehmer", [])),
        response_code=result.get("datev_response_code"),
        response_body=response_body,
    )

    return {
        "dry_run": False,
        "sandbox": result.get("sandbox", False),
        "status": "success",
        "import_id": result.get("import_id"),
        "message": result.get("message"),
        "records_sent": len(payload.get("Arbeitnehmer", [])),
        "log_id": str(log.id),
        "exported_at": log.exported_at.isoformat(),
    }


@router.get(
    "/export/history",
    summary="Historial de exportaciones DATEV",
    description="Lista las exportaciones realizadas, ordenadas de más reciente a más antigua.",
)
def export_history(
    year: Optional[int] = Query(None, description="Filtrar por año"),
    month: Optional[int] = Query(None, ge=1, le=12, description="Filtrar por mes"),
    limit: int = Query(50, ge=1, le=200, description="Máximo de registros a devolver"),
    db: Session = Depends(get_db),
):
    logs = datev_service.get_export_history(db, limit=limit, year=year, month=month)
    return {
        "total": len(logs),
        "exportaciones": [
            {
                "id": str(log.id),
                "year": log.year,
                "month": log.month,
                "exported_by": log.exported_by,
                "exported_at": log.exported_at.isoformat(),
                "status": log.status,
                "records_sent": log.records_sent,
                "response_code": log.response_code,
                "error_message": log.error_message,
                "file_path": log.file_path,
            }
            for log in logs
        ],
    }


@router.post(
    "/export/csv",
    summary="Descargar CSV en formato DATEV Lohn & Gehalt",
    description="""
Genera y descarga un archivo CSV compatible con DATEV Lohn & Gehalt.

**Alternativa offline**: No requiere credenciales OAuth. Puede importarse
manualmente en DATEV desde el módulo de importación DTVF.

Formato: CSV con separador ";" y codificación UTF-8-BOM (requerido por DATEV).
""",
)
def export_csv(body: CsvRequest, db: Session = Depends(get_db)):
    try:
        csv_bytes = datev_service.export_to_csv(db, body.year, body.month)
    except Exception as e:
        raise HTTPException(500, f"Fehler beim Erstellen der CSV: {str(e)}")

    filename = f"datev_lohn_{body.year}-{body.month:02d}.csv"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  STATUS
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/status",
    summary="Estado de la integración DATEV",
    description=(
        "Verifica el estado de la conexión DATEV: "
        "si hay configuración, si el token es válido, y si está en modo sandbox."
    ),
)
def datev_status(db: Session = Depends(get_db)):
    sandbox_env = datev_service._is_sandbox()
    config = datev_service.get_config(db)

    if config is None:
        return {
            "configured": False,
            "sandbox": sandbox_env,
            "status": "not_configured",
            "message": "No hay configuración DATEV. Use POST /datev/config para configurar.",
        }

    # Verificar token
    token_valid = False
    token_expires_in = None
    if config.access_token and config.token_expires_at:
        now = datetime.utcnow()
        if config.token_expires_at > now:
            token_valid = True
            token_expires_in = int((config.token_expires_at - now).total_seconds())

    has_credentials = bool(config.client_id and config.client_secret)
    effective_sandbox = sandbox_env or not has_credentials

    return {
        "configured": True,
        "sandbox": effective_sandbox,
        "sandbox_env": sandbox_env,
        "has_credentials": has_credentials,
        "has_access_token": bool(config.access_token),
        "has_refresh_token": bool(config.refresh_token),
        "token_valid": token_valid,
        "token_expires_in_seconds": token_expires_in,
        "consultant_number": config.consultant_number,
        "client_number": config.client_number,
        "company_name": config.company_name,
        "payroll_type": config.payroll_type,
        "status": "ready" if (token_valid or effective_sandbox) else "token_expired",
        "message": (
            "Modo sandbox activo — exportaciones simuladas sin envío real a DATEV"
            if effective_sandbox
            else (
                "Conexión DATEV operativa"
                if token_valid
                else "Token abgelaufen. Use GET /datev/oauth/authorize para renovar."
            )
        ),
    }
