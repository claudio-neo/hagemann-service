"""
Tareas programadas — APScheduler
  - Backup Telegram cada 2 horas
"""
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

BACKUP_CHAT_ID = 6055586001   # Telegram chat donde se envía el backup

scheduler = BackgroundScheduler(timezone="UTC")


def run_backup():
    """Ejecuta el backup y lo envía a Telegram. Crea su propia sesión de BD."""
    from .database import SessionLocal
    from .routes.import_export import _python_backup
    from .services.audit_service import log_action

    db = SessionLocal()
    try:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        logger.info("Scheduled backup starting — %s", timestamp)
        result = _python_backup(db, BACKUP_CHAT_ID, timestamp)
        logger.info("Scheduled backup OK — %s", result.get("datei"))
    except Exception as exc:
        logger.error("Scheduled backup FAILED: %s", exc, exc_info=True)
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(
        run_backup,
        trigger=IntervalTrigger(hours=2),
        id="telegram_backup",
        name="Backup Telegram cada 2h",
        replace_existing=True,
        misfire_grace_time=300,   # tolera hasta 5 min de retraso
    )
    scheduler.start()
    next_run = scheduler.get_job("telegram_backup").next_run_time
    print(f"[SCHEDULER] Iniciado — backup Telegram cada 2h. Próximo: {next_run}", flush=True)
    logger.info("Scheduler iniciado — backup Telegram cada 2 horas. Próximo: %s", next_run)


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler detenido")
