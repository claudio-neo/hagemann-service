"""
API de Fichajes — entrada / salida / cambio de departamento
Lógica de segmentos por centro de coste dentro de una jornada.
HG-18: integración ArbZG (pausa mínima automática)
HG-19: cierre forzado de jornadas abiertas
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, func
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import datetime, date, timedelta, timezone

from ..database import get_db
from ..models.empleado import Empleado, CentroCoste
from ..models.fichaje import Fichaje, SegmentoTiempo, FuenteFichaje
from ..models.pausa import Pausa
from ..auth import require_permission
from ..permisos import TIMECLOCK_REGISTER, HOURS_CONTROL_TEAM
from ..services.arbzg import calcular_pausa_minima, verificar_jornada_maxima
from ..services.zeitgruppe_service import aplicar_ajuste_zeitgruppe, calcular_minutos_rauch_descontables

router = APIRouter(prefix="/fichajes", tags=["Fichajes"])


# ========== SCHEMAS ==========

class PunchIn(BaseModel):
    """Fichaje de entrada — requiere centro de coste"""
    nfc_tag: Optional[str] = None
    empleado_id: Optional[UUID] = None
    centro_coste_id: UUID
    dispositivo_id: Optional[str] = None
    timestamp: Optional[datetime] = None


class PunchOut(BaseModel):
    """Fichaje de salida"""
    nfc_tag: Optional[str] = None
    empleado_id: Optional[UUID] = None
    dispositivo_id: Optional[str] = None
    timestamp: Optional[datetime] = None


class SwitchDepartment(BaseModel):
    """Cambio de departamento dentro de la misma jornada"""
    nfc_tag: Optional[str] = None
    empleado_id: Optional[UUID] = None
    nuevo_centro_coste_id: UUID
    dispositivo_id: Optional[str] = None
    timestamp: Optional[datetime] = None


# ========== HELPERS ==========

def _utc(dt: datetime) -> str:
    """Devuelve ISO 8601 con sufijo Z para que JS lo interprete como UTC"""
    if dt is None:
        return None
    return dt.isoformat() + "Z"


def _resolve_empleado(db: Session, nfc_tag: str = None, empleado_id: UUID = None) -> Empleado:
    """Busca empleado por NFC tag o ID"""
    if empleado_id:
        emp = db.query(Empleado).filter(Empleado.id == empleado_id).first()
    elif nfc_tag:
        emp = db.query(Empleado).filter(Empleado.nfc_tag == nfc_tag).first()
    else:
        raise HTTPException(400, "nfc_tag oder empleado_id erforderlich")
    if not emp:
        raise HTTPException(404, "Mitarbeiter nicht gefunden")
    if not emp.activo:
        raise HTTPException(403, "Mitarbeiter inaktiv")
    return emp


def _get_open_fichaje(db: Session, empleado_id: UUID) -> Optional[Fichaje]:
    """Obtiene el fichaje abierto (sin salida) del empleado"""
    return (
        db.query(Fichaje)
        .filter(
            Fichaje.empleado_id == empleado_id,
            Fichaje.fecha_salida.is_(None),
        )
        .first()
    )


def _get_open_segment(db: Session, fichaje_id: UUID) -> Optional[SegmentoTiempo]:
    """Obtiene el segmento abierto del fichaje"""
    return (
        db.query(SegmentoTiempo)
        .filter(
            SegmentoTiempo.fichaje_id == fichaje_id,
            SegmentoTiempo.fin.is_(None),
        )
        .first()
    )


def _close_segment(segment: SegmentoTiempo, timestamp: datetime):
    """Cierra un segmento y calcula minutos"""
    segment.fin = timestamp
    diff = timestamp - segment.inicio
    segment.minutos = max(0, int(diff.total_seconds() / 60))


def _calc_total_minutes(db: Session, fichaje_id: UUID) -> int:
    """Suma minutos de todos los segmentos cerrados de un fichaje"""
    result = (
        db.query(func.coalesce(func.sum(SegmentoTiempo.minutos), 0))
        .filter(
            SegmentoTiempo.fichaje_id == fichaje_id,
            SegmentoTiempo.fin.isnot(None),
        )
        .scalar()
    )
    return int(result)


def _segment_dict(seg: SegmentoTiempo, cc: CentroCoste = None) -> dict:
    """Serializa un segmento"""
    return {
        "id": str(seg.id),
        "centro_coste_id": str(seg.centro_coste_id),
        "centro_coste_nombre": cc.nombre if cc else None,
        "centro_coste_codigo": cc.codigo if cc else None,
        "inicio": _utc(seg.inicio) if seg.inicio else None,
        "fin": _utc(seg.fin),
        "minutos": seg.minutos,
    }


# ========== ENDPOINTS ==========

@router.post("/entrada", status_code=201)
def fichar_entrada(data: PunchIn, db: Session = Depends(get_db), _auth=Depends(require_permission(TIMECLOCK_REGISTER))):
    """
    Fichaje de ENTRADA — abre jornada + primer segmento.
    El empleado debe seleccionar un centro de coste.
    """
    emp = _resolve_empleado(db, data.nfc_tag, data.empleado_id)
    ts_real = data.timestamp or datetime.utcnow()

    # C: Ajuste Zeitgruppe — redondear según reglas de grupo horario
    ts, aviso_zeitgruppe = aplicar_ajuste_zeitgruppe(ts_real, emp, db)

    # Verificar que no hay jornada abierta
    open_fich = _get_open_fichaje(db, emp.id)
    if open_fich:
        raise HTTPException(
            409,
            f"Ya hay una jornada abierta (id={open_fich.id}, "
            f"entrada={_utc(open_fich.fecha_entrada)}). "
            f"Ciérrala primero con /fichajes/salida."
        )

    # Verificar centro de coste
    cc = db.query(CentroCoste).filter(
        CentroCoste.id == data.centro_coste_id, CentroCoste.activo == True
    ).first()
    if not cc:
        raise HTTPException(404, "Kostenstelle nicht gefunden oder inaktiv")

    # Crear fichaje
    fichaje = Fichaje(
        empleado_id=emp.id,
        dispositivo_id=data.dispositivo_id,
        fecha_entrada=ts,
        fuente=FuenteFichaje.TABLET if data.dispositivo_id else FuenteFichaje.MANUAL,
    )
    db.add(fichaje)
    db.flush()

    # Crear primer segmento
    segmento = SegmentoTiempo(
        fichaje_id=fichaje.id,
        empleado_id=emp.id,
        centro_coste_id=cc.id,
        inicio=ts,
    )
    db.add(segmento)
    db.commit()

    return {
        "action": "IN",
        "fichaje_id": str(fichaje.id),
        "segmento_id": str(segmento.id),
        "empleado": {
            "id": str(emp.id),
            "id_nummer": emp.id_nummer,
            "nombre": f"{emp.nombre} {emp.apellido or ''}".strip(),
        },
        "centro_coste": {
            "id": str(cc.id),
            "codigo": cc.codigo,
            "nombre": cc.nombre,
        },
        "hora": ts.strftime("%H:%M:%S"),
        "hora_real": ts_real.strftime("%H:%M:%S"),
        "aviso_zeitgruppe": aviso_zeitgruppe,
        "message": f"Eingang registriert — {cc.nombre}"
                   + (f" ⚠ {aviso_zeitgruppe}" if aviso_zeitgruppe else ""),
    }


@router.post("/salida")
def fichar_salida(data: PunchOut, db: Session = Depends(get_db), _auth=Depends(require_permission(TIMECLOCK_REGISTER))):
    """
    Fichaje de SALIDA — cierra último segmento + cierra jornada.
    Calcula totales. HG-18: aplica pausa mínima ArbZG si no hay descanso definido.
    """
    emp = _resolve_empleado(db, data.nfc_tag, data.empleado_id)
    ts = data.timestamp or datetime.utcnow()

    fichaje = _get_open_fichaje(db, emp.id)
    if not fichaje:
        raise HTTPException(404, "Keine offene Schicht für diesen Mitarbeiter")

    # Cerrar segmento activo
    seg_abierto = _get_open_segment(db, fichaje.id)
    if seg_abierto:
        _close_segment(seg_abierto, ts)

    # Calcular minutos trabajados (suma de segmentos)
    fichaje.fecha_salida = ts
    db.flush()  # necesario con autoflush=False para que _calc_total_minutes vea el segmento cerrado
    minutos_brutos = _calc_total_minutes(db, fichaje.id)

    # HG-18: auto-calcular pausa ArbZG si no hay descanso manual registrado
    arbzg_pausa = None
    if not fichaje.minutos_descanso or fichaje.minutos_descanso == 0:
        pausa_minima = calcular_pausa_minima(minutos_brutos)
        if pausa_minima > 0:
            fichaje.minutos_descanso = pausa_minima
            arbzg_pausa = pausa_minima

    fichaje.minutos_trabajados = minutos_brutos

    # HG-18: warning por jornada máxima
    arbzg_warning = verificar_jornada_maxima(minutos_brutos)

    db.commit()

    # Resumen de segmentos
    segmentos = (
        db.query(SegmentoTiempo, CentroCoste)
        .join(CentroCoste, SegmentoTiempo.centro_coste_id == CentroCoste.id)
        .filter(SegmentoTiempo.fichaje_id == fichaje.id)
        .order_by(SegmentoTiempo.inicio)
        .all()
    )

    result = {
        "action": "OUT",
        "fichaje_id": str(fichaje.id),
        "empleado": {
            "id": str(emp.id),
            "id_nummer": emp.id_nummer,
            "nombre": f"{emp.nombre} {emp.apellido or ''}".strip(),
        },
        "segmentos": [_segment_dict(s, cc) for s, cc in segmentos],
        "total_minutos": fichaje.minutos_trabajados,
        "total_formateado": f"{fichaje.minutos_trabajados // 60}:{fichaje.minutos_trabajados % 60:02d}",
        "minutos_descanso": fichaje.minutos_descanso,
        "hora": ts.strftime("%H:%M:%S"),
        "message": f"Salida registrada — {fichaje.minutos_trabajados // 60}h {fichaje.minutos_trabajados % 60}min",
    }
    if arbzg_pausa is not None:
        result["arbzg_pausa_aplicada"] = arbzg_pausa
        result["arbzg_info"] = f"Pausa mínima ArbZG aplicada automáticamente: {arbzg_pausa} min"
    if arbzg_warning:
        result["arbzg_warning"] = arbzg_warning
    return result


@router.post("/cierre-forzado")
def cierre_forzado(
    max_horas: float = Query(24.0, ge=1.0, description="Horas mínimas para considerar jornada abandonada"),
    db: Session = Depends(get_db),
    _auth=Depends(require_permission(HOURS_CONTROL_TEAM)),
):
    """
    HG-19: Cierra forzosamente todas las jornadas abiertas que llevan más de
    `max_horas` horas sin cerrarse.

    Para cada jornada:
    - Calcula salida efectiva: fecha_entrada + min(horas_transcurridas, 10h)
    - Aplica pausa mínima ArbZG si no hay descanso registrado
    - Marca cierre_forzado = True
    - Cierra segmentos abiertos

    Devuelve lista de fichajes cerrados.
    """
    ahora = datetime.utcnow()
    umbral = ahora - timedelta(hours=max_horas)

    fichajes_abiertos = (
        db.query(Fichaje)
        .options(joinedload(Fichaje.empleado))
        .filter(
            Fichaje.fecha_salida.is_(None),
            Fichaje.fecha_entrada <= umbral,
        )
        .all()
    )

    if not fichajes_abiertos:
        return {
            "cerrados": 0,
            "data": [],
            "message": f"No hay jornadas abiertas con más de {max_horas}h",
        }

    cerrados = []

    for fichaje in fichajes_abiertos:
        # Calcular tiempo bruto transcurrido (máx 10h = 600 min)
        minutos_transcurridos = int((ahora - fichaje.fecha_entrada).total_seconds() / 60)
        minutos_cap = min(minutos_transcurridos, 600)  # máx 10h

        # Timestamp de cierre: entrada + cap
        ts_cierre = fichaje.fecha_entrada + timedelta(minutes=minutos_cap)

        # Cerrar segmentos abiertos
        seg_abierto = _get_open_segment(db, fichaje.id)
        if seg_abierto:
            seg_abierto.fin = ts_cierre
            diff_seg = ts_cierre - seg_abierto.inicio
            seg_abierto.minutos = max(0, int(diff_seg.total_seconds() / 60))

        # Flush para que _calc_total_minutes vea los segmentos cerrados (autoflush=False)
        db.flush()

        # Calcular minutos de segmentos cerrados
        minutos_seg = _calc_total_minutes(db, fichaje.id)

        # Aplicar pausa ArbZG si no hay descanso manual
        pausa_aplicada = None
        if not fichaje.minutos_descanso or fichaje.minutos_descanso == 0:
            pausa = calcular_pausa_minima(minutos_cap)
            if pausa > 0:
                fichaje.minutos_descanso = pausa
                pausa_aplicada = pausa

        # Cerrar jornada
        fichaje.fecha_salida = ts_cierre
        fichaje.minutos_trabajados = minutos_seg
        fichaje.cierre_forzado = True
        fichaje.updated_at = ahora

        emp = fichaje.empleado
        cerrados.append({
            "fichaje_id": str(fichaje.id),
            "empleado_id": str(fichaje.empleado_id),
            "empleado_nombre": f"{emp.nombre} {emp.apellido or ''}".strip() if emp else None,
            "fecha_entrada": _utc(fichaje.fecha_entrada),
            "fecha_salida_forzada": _utc(ts_cierre),
            "minutos_transcurridos": minutos_transcurridos,
            "minutos_trabajados": minutos_seg,
            "minutos_descanso": fichaje.minutos_descanso,
            "arbzg_pausa_aplicada": pausa_aplicada,
            "arbzg_warning": verificar_jornada_maxima(minutos_cap),
        })

    db.commit()

    return {
        "cerrados": len(cerrados),
        "max_horas": max_horas,
        "ejecutado_at": _utc(ahora),
        "data": cerrados,
        "message": f"Se cerraron {len(cerrados)} jornada(s) forzosamente",
    }


@router.post("/cambio-departamento")
def cambiar_departamento(data: SwitchDepartment, db: Session = Depends(get_db), _auth=Depends(require_permission(TIMECLOCK_REGISTER))):
    """
    Cambio de departamento — cierra segmento actual, abre nuevo.
    La jornada sigue abierta.
    """
    emp = _resolve_empleado(db, data.nfc_tag, data.empleado_id)
    ts = data.timestamp or datetime.utcnow()

    fichaje = _get_open_fichaje(db, emp.id)
    if not fichaje:
        raise HTTPException(404, "Keine offene Schicht. Bitte zuerst einstempeln.")

    # Verificar nuevo centro de coste
    nuevo_cc = db.query(CentroCoste).filter(
        CentroCoste.id == data.nuevo_centro_coste_id, CentroCoste.activo == True
    ).first()
    if not nuevo_cc:
        raise HTTPException(404, "Kostenstelle nicht gefunden oder inaktiv")

    # Cerrar segmento actual
    seg_abierto = _get_open_segment(db, fichaje.id)
    old_cc_nombre = None
    if seg_abierto:
        if seg_abierto.centro_coste_id == nuevo_cc.id:
            raise HTTPException(409, f"Bereits tätig in {nuevo_cc.nombre}")
        old_cc = db.query(CentroCoste).filter(
            CentroCoste.id == seg_abierto.centro_coste_id
        ).first()
        old_cc_nombre = old_cc.nombre if old_cc else "?"
        _close_segment(seg_abierto, ts)

    # Abrir nuevo segmento
    nuevo_seg = SegmentoTiempo(
        fichaje_id=fichaje.id,
        empleado_id=emp.id,
        centro_coste_id=nuevo_cc.id,
        inicio=ts,
    )
    db.add(nuevo_seg)
    db.commit()

    return {
        "action": "SWITCH",
        "fichaje_id": str(fichaje.id),
        "segmento_cerrado": _segment_dict(seg_abierto) if seg_abierto else None,
        "segmento_nuevo": {
            "id": str(nuevo_seg.id),
            "centro_coste": nuevo_cc.nombre,
            "inicio": ts.strftime("%H:%M:%S"),
        },
        "message": f"Cambio: {old_cc_nombre or '?'} → {nuevo_cc.nombre}",
    }


@router.get("/")
def listar_fichajes(
    empleado_id: Optional[UUID] = None,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    abiertos: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _auth=Depends(require_permission(HOURS_CONTROL_TEAM)),
):
    """Lista fichajes con filtros"""
    query = (
        db.query(Fichaje)
        .options(joinedload(Fichaje.empleado), joinedload(Fichaje.segmentos))
    )
    if empleado_id:
        query = query.filter(Fichaje.empleado_id == empleado_id)
    if desde:
        query = query.filter(Fichaje.fecha_entrada >= datetime.combine(desde, datetime.min.time()))
    if hasta:
        query = query.filter(Fichaje.fecha_entrada <= datetime.combine(hasta, datetime.max.time()))
    if abiertos is True:
        query = query.filter(Fichaje.fecha_salida.is_(None))
    elif abiertos is False:
        query = query.filter(Fichaje.fecha_salida.isnot(None))

    total = query.count()
    fichajes = (
        query.order_by(Fichaje.fecha_entrada.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return {
        "data": [
            {
                "id": str(f.id),
                "empleado_id": str(f.empleado_id),
                "empleado_nombre": f"{f.empleado.nombre} {f.empleado.apellido or ''}".strip() if f.empleado else None,
                "empleado_id_nummer": f.empleado.id_nummer if f.empleado else None,
                "fecha_entrada": _utc(f.fecha_entrada),
                "fecha_salida": _utc(f.fecha_salida),
                "minutos_trabajados": f.minutos_trabajados,
                "minutos_descanso": f.minutos_descanso,
                "segmentos": [
                    {
                        "centro_coste_id": str(s.centro_coste_id),
                        "inicio": _utc(s.inicio),
                        "fin": _utc(s.fin),
                        "minutos": s.minutos,
                    }
                    for s in f.segmentos
                ],
                "fuente": f.fuente if f.fuente else None,
                "cierre_forzado": f.cierre_forzado,
            }
            for f in fichajes
        ],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit,
        },
    }


@router.get("/abiertos")
def fichajes_abiertos(db: Session = Depends(get_db), _auth=Depends(require_permission(TIMECLOCK_REGISTER))):
    """Lista fichajes abiertos (empleados que están trabajando ahora)"""
    fichajes = (
        db.query(Fichaje)
        .options(joinedload(Fichaje.empleado), joinedload(Fichaje.segmentos))
        .filter(Fichaje.fecha_salida.is_(None))
        .all()
    )

    results = []
    for f in fichajes:
        seg_activo = next((s for s in f.segmentos if s.fin is None), None)
        cc = None
        if seg_activo:
            cc = db.query(CentroCoste).filter(
                CentroCoste.id == seg_activo.centro_coste_id
            ).first()
        results.append({
            "fichaje_id": str(f.id),
            "empleado": {
                "id": str(f.empleado.id),
                "id_nummer": f.empleado.id_nummer,
                "nombre": f"{f.empleado.nombre} {f.empleado.apellido or ''}".strip(),
            },
            "entrada": _utc(f.fecha_entrada),
            "centro_coste_actual": {
                "id": str(cc.id),
                "nombre": cc.nombre,
                "codigo": cc.codigo,
            } if cc else None,
            "segmentos_count": len(f.segmentos),
        })

    return {"data": results, "total": len(results)}


# ========== PAUSEN (Raucherpause / Mittagspause) ==========

class PauseStart(BaseModel):
    empleado_id: Optional[UUID] = None
    nfc_tag: Optional[str] = None
    tipo: str = "RAUCH"   # RAUCH | MITTAG | SONSTIG
    timestamp: Optional[datetime] = None

class PauseEnd(BaseModel):
    empleado_id: Optional[UUID] = None
    nfc_tag: Optional[str] = None
    timestamp: Optional[datetime] = None


@router.post("/pausa/inicio")
def iniciar_pausa(data: PauseStart, db: Session = Depends(get_db), _auth=Depends(require_permission(TIMECLOCK_REGISTER))):
    """Inicia una pausa (Raucherpause, etc.) dentro de la jornada abierta."""
    emp = _resolve_empleado(db, data.nfc_tag, data.empleado_id)
    ts = data.timestamp or datetime.utcnow()

    fichaje = _get_open_fichaje(db, emp.id)
    if not fichaje:
        raise HTTPException(404, "Keine offene Schicht — bitte zuerst einstempeln")

    # Verificar que no hay otra pausa abierta
    pausa_abierta = db.query(Pausa).filter(
        Pausa.fichaje_id == fichaje.id,
        Pausa.fin.is_(None),
    ).first()
    if pausa_abierta:
        raise HTTPException(409, f"Es läuft bereits eine Pause ({pausa_abierta.tipo}). Bitte zuerst beenden.")

    pausa = Pausa(
        fichaje_id=fichaje.id,
        empleado_id=emp.id,
        tipo=data.tipo.upper(),
        inicio=ts,
        descontado=(data.tipo.upper() == "RAUCH"),  # Raucherpause siempre descontable
    )
    db.add(pausa)
    db.commit()
    db.refresh(pausa)

    tipo_label = {"RAUCH": "Raucherpause", "MITTAG": "Mittagspause", "SONSTIG": "Pause"}.get(pausa.tipo, pausa.tipo)

    return {
        "action": "PAUSE_START",
        "pausa_id": str(pausa.id),
        "tipo": pausa.tipo,
        "tipo_label": tipo_label,
        "inicio": _utc(pausa.inicio),
        "empleado": {
            "id": str(emp.id),
            "nombre": f"{emp.nombre} {emp.apellido or ''}".strip(),
        },
        "message": f"{tipo_label} gestartet um {ts.strftime('%H:%M')}",
    }


@router.post("/pausa/fin")
def finalizar_pausa(data: PauseEnd, db: Session = Depends(get_db), _auth=Depends(require_permission(TIMECLOCK_REGISTER))):
    """Finaliza la pausa activa del empleado."""
    emp = _resolve_empleado(db, data.nfc_tag, data.empleado_id)
    ts = data.timestamp or datetime.utcnow()

    fichaje = _get_open_fichaje(db, emp.id)
    if not fichaje:
        raise HTTPException(404, "Keine offene Schicht")

    pausa = db.query(Pausa).filter(
        Pausa.fichaje_id == fichaje.id,
        Pausa.fin.is_(None),
    ).first()
    if not pausa:
        raise HTTPException(404, "Keine aktive Pause")

    pausa.fin = ts
    pausa.minutos = max(1, int((ts - pausa.inicio).total_seconds() // 60))
    db.commit()

    tipo_label = {"RAUCH": "Raucherpause", "MITTAG": "Mittagspause", "SONSTIG": "Pause"}.get(pausa.tipo, pausa.tipo)

    return {
        "action": "PAUSE_END",
        "pausa_id": str(pausa.id),
        "tipo": pausa.tipo,
        "tipo_label": tipo_label,
        "inicio": _utc(pausa.inicio),
        "fin": _utc(pausa.fin),
        "minutos": pausa.minutos,
        "message": f"{tipo_label} beendet — {pausa.minutos} Minuten",
    }


@router.get("/pausa/estado/{empleado_id}")
def estado_pausa(empleado_id: UUID, db: Session = Depends(get_db), _auth=Depends(require_permission(TIMECLOCK_REGISTER))):
    """Estado de pausa del empleado: si tiene pausa activa y cuánto lleva."""
    fichaje = _get_open_fichaje(db, empleado_id)
    if not fichaje:
        return {"en_pausa": False, "fichaje_abierto": False}

    pausa = db.query(Pausa).filter(
        Pausa.fichaje_id == fichaje.id,
        Pausa.fin.is_(None),
    ).first()

    pausen_hoy = db.query(Pausa).filter(
        Pausa.fichaje_id == fichaje.id,
        Pausa.fin.isnot(None),
    ).all()

    total_minutos_rauch = sum(p.minutos or 0 for p in pausen_hoy if p.tipo == "RAUCH")

    return {
        "en_pausa": pausa is not None,
        "fichaje_abierto": True,
        "pausa_activa": {
            "id": str(pausa.id),
            "tipo": pausa.tipo,
            "inicio": _utc(pausa.inicio),
            "minutos_transcurridos": int((datetime.utcnow() - pausa.inicio).total_seconds() // 60),
        } if pausa else None,
        "total_raucherpausen_heute": len([p for p in pausen_hoy if p.tipo == "RAUCH"]),
        "total_minutos_rauch": total_minutos_rauch,
    }
