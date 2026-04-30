"""
Import/Export de empleados via Excel/CSV + Backup Telegram (HG-Plan E, H)
"""
import csv
import io
import os
import subprocess
import tempfile
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from ..database import get_db
from ..models.empleado import Empleado, Grupo, CentroCoste, Zeitgruppe
from ..services.audit_service import log_action

router = APIRouter(tags=["Import/Export"])

# ── Telegram Backup config ───────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv(
    "BACKUP_TELEGRAM_TOKEN",
    "8631473423:AAFFeU426CXzYdDDQEdvmVhHL48U8cmLGTU"
)
TELEGRAM_CHAT_ID = os.getenv("BACKUP_TELEGRAM_CHAT_ID", "")


# ── Import Excel/CSV ────────────────────────────────────
@router.post("/import/mitarbeiter")
async def import_mitarbeiter(
    file: UploadFile = File(...),
    dry_run: bool = Query(False, description="Nur validieren, nicht speichern"),
    db: Session = Depends(get_db),
):
    """
    Importar empleados desde Excel (.xlsx) o CSV (.csv).
    Formato esperado (Importvorlage Hagemann):
      Systemnummer | Vorname | Nachname | Personalnummer | Benutzer-ID |
      Benutzerstatus | Transponder-ID | Beginn der Berechnung | Zeitgruppe |
      Abteilung | Kostenstelle | Mandat | Firmenbereich
    """
    filename = file.filename or ""
    content = await file.read()

    if filename.endswith(".xlsx"):
        rows = _parse_xlsx(content)
    elif filename.endswith(".csv"):
        rows = _parse_csv(content)
    else:
        raise HTTPException(400, "Nur .xlsx oder .csv Dateien erlaubt")

    if not rows:
        raise HTTPException(400, "Keine Daten in der Datei gefunden")

    # Cache lookups
    gruppen = {g.nombre: g for g in db.query(Grupo).all()}
    kostenstellen = {k.nombre: k for k in db.query(CentroCoste).all()}
    zeitgruppen_db = {z.nombre: z for z in db.query(Zeitgruppe).all()}
    existing_ids = {
        e.id_nummer for e in
        db.query(Empleado.id_nummer).all()
    }

    results = {"erstellt": 0, "aktualisiert": 0, "fehler": [], "details": []}
    new_gruppen = set()
    new_kostenstellen = set()

    for i, row in enumerate(rows, start=2):
        try:
            sys_nr = int(row.get("Systemnummer") or row.get("systemnummer", 0))
            if not sys_nr:
                results["fehler"].append(f"Zeile {i}: Systemnummer fehlt")
                continue

            vorname = (row.get("Vorname") or row.get("vorname", "")).strip()
            nachname = (row.get("Nachname") or row.get("nachname", "")).strip()
            if not vorname:
                results["fehler"].append(f"Zeile {i}: Vorname fehlt")
                continue

            personal_nr = _safe_int(row.get("Personalnummer") or row.get("personalnummer"))
            benutzer_id = _safe_int(row.get("Benutzer-ID") or row.get("benutzer_id"))
            transponder = (row.get("Transponder-ID") or row.get("transponder_id", "")).strip() or None
            beginn = _parse_date(row.get("Beginn der Berechnung") or row.get("beginn_berechnung"))
            abteilung_name = (row.get("Abteilung") or row.get("abteilung", "")).strip()
            ks_name = (row.get("Kostenstelle") or row.get("kostenstelle", "")).strip()
            zg_name = (row.get("Zeitgruppe") or row.get("zeitgruppe", "")).strip()
            mandat = (row.get("Mandat") or row.get("mandat", "<Keine>")).strip()
            firmenbereich = (row.get("Firmenbereich") or row.get("firmenbereich", "<Keine>")).strip()

            # Auto-create Abteilung if needed
            grupo = None
            if abteilung_name and abteilung_name != "<Keine>":
                if abteilung_name not in gruppen:
                    if not dry_run:
                        g = Grupo(nombre=abteilung_name)
                        db.add(g)
                        db.flush()
                        gruppen[abteilung_name] = g
                    new_gruppen.add(abteilung_name)
                grupo = gruppen.get(abteilung_name)

            # Auto-create Kostenstelle if needed
            ks = None
            if ks_name and ks_name != "<Keine>":
                if ks_name not in kostenstellen:
                    if not dry_run:
                        c = CentroCoste(codigo=ks_name[:20], nombre=ks_name)
                        db.add(c)
                        db.flush()
                        kostenstellen[ks_name] = c
                    new_kostenstellen.add(ks_name)
                ks = kostenstellen.get(ks_name)

            # Lookup Zeitgruppe
            zg = zeitgruppen_db.get(zg_name)

            if sys_nr in existing_ids:
                # Update existing
                if not dry_run:
                    emp = db.query(Empleado).filter(Empleado.id_nummer == sys_nr).first()
                    if emp:
                        emp.nombre = vorname
                        emp.apellido = nachname
                        emp.personalnummer = personal_nr
                        emp.benutzer_id = benutzer_id
                        emp.nfc_tag = transponder
                        emp.beginn_berechnung = beginn
                        emp.grupo_id = grupo.id if grupo else emp.grupo_id
                        emp.kostenstelle_id = ks.id if ks else emp.kostenstelle_id
                        emp.zeitgruppe_id = zg.id if zg else emp.zeitgruppe_id
                        emp.mandat = mandat
                        emp.firmenbereich = firmenbereich
                results["aktualisiert"] += 1
                results["details"].append(f"#{sys_nr} {vorname} {nachname} — aktualisiert")
            else:
                # Create new
                if not dry_run:
                    emp = Empleado(
                        id_nummer=sys_nr,
                        personalnummer=personal_nr,
                        benutzer_id=benutzer_id,
                        nombre=vorname,
                        apellido=nachname,
                        nfc_tag=transponder,
                        beginn_berechnung=beginn,
                        grupo_id=grupo.id if grupo else None,
                        kostenstelle_id=ks.id if ks else None,
                        zeitgruppe_id=zg.id if zg else None,
                        mandat=mandat,
                        firmenbereich=firmenbereich,
                        fecha_alta=beginn,
                    )
                    db.add(emp)
                    existing_ids.add(sys_nr)
                results["erstellt"] += 1
                results["details"].append(f"#{sys_nr} {vorname} {nachname} — erstellt")

        except Exception as e:
            results["fehler"].append(f"Zeile {i}: {str(e)}")

    if not dry_run:
        log_action(
            db, accion="IMPORT", entidad_tipo="empleado",
            descripcion=f"Import: {results['erstellt']} erstellt, {results['aktualisiert']} aktualisiert, {len(results['fehler'])} Fehler",
            usuario_nick="admin",
        )
        db.commit()

    results["dry_run"] = dry_run
    results["neue_abteilungen"] = list(new_gruppen)
    results["neue_kostenstellen"] = list(new_kostenstellen)
    return results


# ── Backup Telegram ──────────────────────────────────────
@router.post("/backup/telegram")
def backup_to_telegram(
    chat_id: str = Query(..., description="Telegram Chat-ID für den Backup"),
    db: Session = Depends(get_db),
):
    """
    Exportar backup de la DB y enviar por Telegram.
    Usa pg_dump del container postgres.
    """
    db_url = os.getenv("DATABASE_URL", "")
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dump_file = f"/tmp/hagemann_backup_{ts}.sql.gz"

    try:
        # pg_dump via subprocess
        cmd = (
            f"pg_dump -h postgres -U postgres -d neofreight "
            f"--schema=hagemann --no-owner --no-acl "
            f"| gzip > {dump_file}"
        )
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            env={**os.environ, "PGPASSWORD": "localdev"},
            timeout=60,
        )
        if result.returncode != 0:
            raise HTTPException(500, f"pg_dump fehlgeschlagen: {result.stderr}")

        file_size = os.path.getsize(dump_file)

        # Send via Telegram Bot API
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        with open(dump_file, "rb") as f:
            resp = httpx.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": f"🗄 Hagemann Backup\n📅 {ts}\n📦 {file_size // 1024} KB",
                },
                files={"document": (f"hagemann_{ts}.sql.gz", f, "application/gzip")},
                timeout=30,
            )

        if resp.status_code != 200:
            raise HTTPException(500, f"Telegram API Fehler: {resp.text}")

        log_action(
            db, accion="BACKUP", entidad_tipo="system",
            descripcion=f"Backup an Telegram gesendet ({file_size // 1024} KB)",
            usuario_nick="system",
        )
        db.commit()

        # Cleanup
        os.unlink(dump_file)

        return {
            "status": "ok",
            "message": f"Backup erfolgreich gesendet ({file_size // 1024} KB)",
            "timestamp": ts,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Backup fehlgeschlagen: {str(e)}")


# ── Helpers ──────────────────────────────────────────────
def _parse_xlsx(content: bytes) -> list[dict]:
    """Parse Excel file to list of dicts. Busca hoja 'Importvorlage' o usa la primera."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    # Prefer sheet named 'Importvorlage', fallback to first
    if "Importvorlage" in wb.sheetnames:
        ws = wb["Importvorlage"]
    else:
        ws = wb[wb.sheetnames[0]]
    rows_iter = ws.iter_rows(values_only=True)
    headers = [str(h or "").strip() for h in next(rows_iter)]
    result = []
    for row in rows_iter:
        if all(v is None for v in row):
            continue
        result.append(dict(zip(headers, row)))
    return result


def _parse_csv(content: bytes) -> list[dict]:
    """Parse CSV file to list of dicts."""
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    return list(reader)


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_date(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if hasattr(val, "date"):
        return val.date()
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
