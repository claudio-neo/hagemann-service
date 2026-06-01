"""
Notificaciones a la Personalabteilung (RRHH) — Hagemann.

Avisa cuando se registra una Krankmeldung por tres vías:
  - Telegram (reutiliza el bot existente)   → settings.hr_telegram_chat_id
  - Email SMTP                               → settings.hr_email
  - Tarjeta/badge en el panel admin          → vía endpoint de Krankmeldungen recientes

Todas las vías son best-effort: si fallan o no están configuradas, se registra
un aviso en el log pero NUNCA se interrumpe el flujo de creación de la baja.
"""
import logging
import smtplib
from email.message import EmailMessage
from datetime import datetime

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)


def _mensaje_krankmeldung(empleado_nombre: str, solicitud) -> str:
    tipo = getattr(solicitud, "tipo_ausencia", "BAJA_MEDICA")
    return (
        "🤒 Neue Krankmeldung\n"
        f"Mitarbeiter: {empleado_nombre}\n"
        f"Art: {tipo}\n"
        f"Zeitraum: {solicitud.fecha_inicio} – {solicitud.fecha_fin}\n"
        f"Tage: {solicitud.dias}"
        + ("\nHinweis: ½ Tag" if getattr(solicitud, "medio_dia", False) else "")
    )


def _enviar_telegram(texto: str) -> bool:
    s = get_settings()
    if not s.hr_telegram_chat_id or not s.telegram_bot_token:
        return False
    try:
        url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
        with httpx.Client(timeout=10) as client:
            r = client.post(url, json={"chat_id": s.hr_telegram_chat_id, "text": texto})
            r.raise_for_status()
        return True
    except Exception as e:
        logger.warning("Krankmeldung Telegram fallida: %s", e)
        return False


def _enviar_email(asunto: str, cuerpo: str) -> bool:
    s = get_settings()
    if not s.smtp_host or not s.hr_email:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = asunto
        msg["From"] = s.smtp_from
        msg["To"] = s.hr_email
        msg.set_content(cuerpo)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as server:
            if s.smtp_use_tls:
                server.starttls()
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.warning("Krankmeldung Email fallida: %s", e)
        return False


def notificar_krankmeldung(empleado_nombre: str, solicitud) -> dict:
    """
    Notifica a RRHH una Krankmeldung por Telegram y email (best-effort).
    La tarjeta del panel admin se alimenta del endpoint de Krankmeldungen recientes,
    no requiere envío aquí. Nunca lanza excepción.

    Returns: {"telegram": bool, "email": bool}
    """
    texto = _mensaje_krankmeldung(empleado_nombre, solicitud)
    resultado = {
        "telegram": _enviar_telegram(texto),
        "email": _enviar_email(
            asunto=f"Krankmeldung: {empleado_nombre}",
            cuerpo=texto,
        ),
    }
    logger.info(
        "Krankmeldung notificada (%s): telegram=%s email=%s",
        empleado_nombre, resultado["telegram"], resultado["email"],
    )
    return resultado
