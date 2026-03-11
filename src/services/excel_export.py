"""
Exportación Excel — Hagemann Stundenkonto
Genera reporte mensual de saldos de horas en formato .xlsx
"""
import io
from typing import Optional
from uuid import UUID
from datetime import date

from sqlalchemy.orm import Session
from sqlalchemy import func

try:
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_OK = True
except ImportError:
    OPENPYXL_OK = False

from ..models.empleado import Empleado
from ..models.saldo_horas import SaldoHorasMensual
from ..models.vacaciones import SolicitudVacaciones, TipoAusencia, EstadoSolicitud, Festivo


# ─── Colores ────────────────────────────────────────────────────────────────
HEADER_BG   = "2C3E50"   # azul oscuro
HEADER_FG   = "FFFFFF"   # blanco
ROW_NEG_BG  = "FDECEA"   # rojo claro → diferencia negativa
TOTAL_BG    = "D5D8DC"   # gris
ALT_ROW_BG  = "EBF5FB"   # azul muy claro (filas pares)

HEADERS = [
    "Personalnr.", "Nachname", "Vorname",
    "Planstunden", "Iststunden", "Differenz",
    "Saldo", "Krankheitst.", "Urlaubst.", "Feiertage",
]


def _make_fill(hex_color: str) -> "PatternFill":
    return PatternFill(start_color=hex_color, end_color=hex_color, fill_type="solid")


def _thin_border() -> "Border":
    thin = Side(style="thin", color="BDBDBD")
    return Border(left=thin, right=thin, top=thin, bottom=thin)


def _count_ausencia(
    db: Session,
    empleado_id: UUID,
    year: int,
    month: int,
    tipo: str,
) -> int:
    """Cuenta días de ausencia de un tipo dado en el mes."""
    mes_inicio = date(year, month, 1)
    # Último día del mes
    if month == 12:
        mes_fin = date(year + 1, 1, 1)
    else:
        mes_fin = date(year, month + 1, 1)

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


def _count_festivos(db: Session, year: int, month: int) -> int:
    """Cuenta festivos activos en el mes (días laborables)."""
    mes_inicio = date(year, month, 1)
    if month == 12:
        mes_fin = date(year + 1, 1, 1)
    else:
        mes_fin = date(year, month + 1, 1)

    count = (
        db.query(func.count(Festivo.id))
        .filter(
            Festivo.activo == True,
            Festivo.fecha >= mes_inicio,
            Festivo.fecha < mes_fin,
        )
        .scalar()
    )
    return int(count or 0)


def generar_reporte_mensual(
    db: Session,
    year: int,
    month: int,
    empleado_ids: Optional[list] = None,
) -> bytes:
    """
    Genera el reporte mensual de horas en formato .xlsx y devuelve los bytes.

    Columnas: Personalnr., Nachname, Vorname, Planstunden, Iststunden,
              Differenz, Saldo, Krankheitst., Urlaubst., Feiertage
    """
    if not OPENPYXL_OK:
        raise RuntimeError("openpyxl no está instalado. Añadir a requirements.txt")

    # ── 1. Consultar saldos del mes ─────────────────────────────────────────
    query = (
        db.query(SaldoHorasMensual, Empleado)
        .join(Empleado, SaldoHorasMensual.empleado_id == Empleado.id)
        .filter(
            SaldoHorasMensual.anio == year,
            SaldoHorasMensual.mes == month,
        )
    )
    if empleado_ids:
        query = query.filter(SaldoHorasMensual.empleado_id.in_(empleado_ids))

    rows = query.order_by(Empleado.id_nummer).all()

    # ── 2. Festivos del mes (igual para todos) ──────────────────────────────
    festivos_mes = _count_festivos(db, year, month)

    # ── 3. Construir workbook ────────────────────────────────────────────────
    wb = Workbook()
    ws = wb.active
    ws.title = f"Stunden {year}-{month:02d}"

    header_fill  = _make_fill(HEADER_BG)
    total_fill   = _make_fill(TOTAL_BG)
    neg_fill     = _make_fill(ROW_NEG_BG)
    alt_fill     = _make_fill(ALT_ROW_BG)
    border       = _thin_border()

    # Fila de cabecera (fila 1)
    for col_idx, header in enumerate(HEADERS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = header_fill
        cell.font = Font(bold=True, color=HEADER_FG, size=10)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    ws.row_dimensions[1].height = 20

    # ── 4. Filas de datos ────────────────────────────────────────────────────
    totals = {k: 0.0 for k in
              ["plan", "ist", "diff", "saldo", "krank", "urlaub", "feiertage"]}

    for row_idx, (saldo, emp) in enumerate(rows, start=2):
        plan  = float(saldo.horas_planificadas or 0)
        ist   = float(saldo.horas_reales or 0)
        diff  = ist - plan
        saldo_val = float(saldo.saldo_final or 0)

        krank  = _count_ausencia(db, emp.id, year, month, TipoAusencia.BAJA_MEDICA)
        urlaub = _count_ausencia(db, emp.id, year, month, TipoAusencia.VACACIONES)

        data = [
            emp.id_nummer,
            emp.apellido or "",
            emp.nombre,
            round(plan, 2),
            round(ist, 2),
            round(diff, 2),
            round(saldo_val, 2),
            krank,
            urlaub,
            festivos_mes,
        ]

        is_negative = diff < 0
        row_fill = neg_fill if is_negative else (alt_fill if row_idx % 2 == 0 else None)

        for col_idx, value in enumerate(data, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = border
            cell.alignment = Alignment(
                horizontal="right" if col_idx > 3 else "left",
                vertical="center",
            )
            cell.font = Font(size=10)
            if row_fill:
                cell.fill = row_fill

        # Acumular totales
        totals["plan"]      += plan
        totals["ist"]       += ist
        totals["diff"]      += diff
        totals["saldo"]     += saldo_val
        totals["krank"]     += krank
        totals["urlaub"]    += urlaub
        totals["feiertage"] += festivos_mes

    # ── 5. Fila de totales ────────────────────────────────────────────────────
    total_row = len(rows) + 2
    total_data = [
        "GESAMT", "", "",
        round(totals["plan"], 2),
        round(totals["ist"], 2),
        round(totals["diff"], 2),
        round(totals["saldo"], 2),
        int(totals["krank"]),
        int(totals["urlaub"]),
        int(totals["feiertage"]),
    ]
    for col_idx, value in enumerate(total_data, start=1):
        cell = ws.cell(row=total_row, column=col_idx, value=value)
        cell.fill = total_fill
        cell.font = Font(bold=True, size=10)
        cell.border = border
        cell.alignment = Alignment(
            horizontal="right" if col_idx > 3 else "left",
            vertical="center",
        )

    ws.row_dimensions[total_row].height = 18

    # ── 6. Auto-width columnas ────────────────────────────────────────────────
    for col_idx in range(1, len(HEADERS) + 1):
        max_len = len(HEADERS[col_idx - 1])
        for row_idx in range(2, total_row + 1):
            val = ws.cell(row=row_idx, column=col_idx).value
            if val is not None:
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 3

    # ── 7. Freeze header row ──────────────────────────────────────────────────
    ws.freeze_panes = "A2"

    # ── 8. Serializar a bytes ─────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def preview_reporte_mensual(
    db: Session,
    year: int,
    month: int,
    empleado_ids: Optional[list] = None,
) -> dict:
    """
    Devuelve preview JSON de los datos que se exportarían en Excel.
    """
    query = (
        db.query(SaldoHorasMensual, Empleado)
        .join(Empleado, SaldoHorasMensual.empleado_id == Empleado.id)
        .filter(
            SaldoHorasMensual.anio == year,
            SaldoHorasMensual.mes == month,
        )
    )
    if empleado_ids:
        query = query.filter(SaldoHorasMensual.empleado_id.in_(empleado_ids))

    rows = query.order_by(Empleado.id_nummer).all()
    festivos_mes = _count_festivos(db, year, month)

    data = []
    for saldo, emp in rows:
        plan = float(saldo.horas_planificadas or 0)
        ist  = float(saldo.horas_reales or 0)
        data.append({
            "personalnr":   emp.id_nummer,
            "nachname":     emp.apellido or "",
            "vorname":      emp.nombre,
            "planstunden":  round(plan, 2),
            "iststunden":   round(ist, 2),
            "differenz":    round(ist - plan, 2),
            "saldo":        round(float(saldo.saldo_final or 0), 2),
            "krankheitstage": _count_ausencia(db, emp.id, year, month, TipoAusencia.BAJA_MEDICA),
            "urlaubstage":    _count_ausencia(db, emp.id, year, month, TipoAusencia.VACACIONES),
            "feiertage":    festivos_mes,
        })

    totals = {
        "planstunden":    round(sum(r["planstunden"] for r in data), 2),
        "iststunden":     round(sum(r["iststunden"] for r in data), 2),
        "differenz":      round(sum(r["differenz"] for r in data), 2),
        "saldo":          round(sum(r["saldo"] for r in data), 2),
        "krankheitstage": sum(r["krankheitstage"] for r in data),
        "urlaubstage":    sum(r["urlaubstage"] for r in data),
    }

    return {
        "year": year,
        "month": month,
        "total_empleados": len(data),
        "festivos_mes": festivos_mes,
        "empleados": data,
        "totales": totals,
    }
