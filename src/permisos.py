"""
Permisos granulares del sistema Hagemann.

Fuente de verdad: Stammdaten del importvorlage-hagemann.xlsx.
  Admin              — Zugriff ohne Einschränkungen
  Schichtführer      — Stundenfreigabe, Stundenkontrolle, Urlaubsübersicht equipo,
                       elegir Stellvertreter
  Stv. Schichtführer — Como Benutzer; permisos de Schichtführer solo al sustituir
  Benutzer           — Login/Logout, Raucherpause, Urlaubsantrag, propias horas/vacaciones
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from .models.usuario import Usuario

from .models.usuario import (
    ROLE_ADMIN,
    ROLE_SCHICHTFUEHRER,
    ROLE_STV_SCHICHTFUEHRER,
    ROLE_BENUTZER,
)

# ── Constantes de permiso ─────────────────────────────────────────────────────

# Fichajes propios
FICHAJES_REGISTRAR   = "fichajes:registrar"    # login / logout / Raucherpause / FZA
FICHAJES_VER_PROPIOS = "fichajes:ver_propios"

# Horas — nivel Schichtführer
HORAS_LIBERAR_EQUIPO   = "horas:liberar_equipo"    # Stundenfreigabe
HORAS_CONTROLAR_EQUIPO = "horas:controlar_equipo"  # Stundenkontrolle

# Vacaciones
VACACIONES_SOLICITAR  = "vacaciones:solicitar"   # Urlaubsantrag stellen
VACACIONES_VER_PROPIAS = "vacaciones:ver_propias"
VACACIONES_VER_EQUIPO  = "vacaciones:ver_equipo"  # Urlaubsübersicht seines Teams
VACACIONES_APROBAR     = "vacaciones:aprobar"     # nivel 1 (Schichtführer)

# Stellvertreter
STELLVERTRETER_ASIGNAR = "stellvertreter:asignar"  # Schichtführer elige su sustituto

# Correcciones y aprobaciones
CORRECCIONES_REVISAR = "correcciones:revisar"
APROBACIONES_N1      = "aprobaciones:nivel1"
APROBACIONES_N2      = "aprobaciones:nivel2"

# Reportes y exportación
REPORTES_VER = "reportes:ver"
EXPORTAR     = "exportacion:exportar"

# Administración
USUARIOS_ADMIN   = "usuarios:admin"
TURNOS_ESCRIBIR  = "turnos:escribir"
EMPLEADOS_EDITAR = "empleados:editar"


# ── Mapa rol → permisos base ──────────────────────────────────────────────────

_BASE_BENUTZER: frozenset[str] = frozenset({
    FICHAJES_REGISTRAR,
    FICHAJES_VER_PROPIOS,
    VACACIONES_SOLICITAR,
    VACACIONES_VER_PROPIAS,
})

_BASE_SCHICHTFUEHRER: frozenset[str] = _BASE_BENUTZER | frozenset({
    HORAS_LIBERAR_EQUIPO,
    HORAS_CONTROLAR_EQUIPO,
    VACACIONES_VER_EQUIPO,
    VACACIONES_APROBAR,
    CORRECCIONES_REVISAR,
    APROBACIONES_N1,
    REPORTES_VER,
    STELLVERTRETER_ASIGNAR,
})

_BASE_ADMIN: frozenset[str] = _BASE_SCHICHTFUEHRER | frozenset({
    APROBACIONES_N2,
    EXPORTAR,
    USUARIOS_ADMIN,
    TURNOS_ESCRIBIR,
    EMPLEADOS_EDITAR,
})

PERMISOS_POR_ROL: dict[int, frozenset[str]] = {
    ROLE_ADMIN:             _BASE_ADMIN,
    ROLE_SCHICHTFUEHRER:    _BASE_SCHICHTFUEHRER,
    ROLE_STV_SCHICHTFUEHRER: _BASE_BENUTZER,   # ampliados dinámicamente si hay ausencia
    ROLE_BENUTZER:          _BASE_BENUTZER,
}

# Permisos que el Stv. Schichtführer hereda cuando su Schichtführer está ausente
PERMISOS_DELEGADOS_SCHICHTFUEHRER: frozenset[str] = frozenset({
    HORAS_LIBERAR_EQUIPO,
    HORAS_CONTROLAR_EQUIPO,
    VACACIONES_VER_EQUIPO,
    VACACIONES_APROBAR,
    CORRECCIONES_REVISAR,
    APROBACIONES_N1,
    REPORTES_VER,
})


# ── Permisos efectivos (resuelve delegación automática) ───────────────────────

def permisos_efectivos(user: "Usuario", db: "Session") -> frozenset[str]:
    """
    Devuelve el set de permisos reales del usuario, incluyendo los delegados
    si el Stv. Schichtführer está sustituyendo a un Schichtführer ausente hoy.
    """
    base = PERMISOS_POR_ROL.get(user.role, frozenset())

    if user.role == ROLE_STV_SCHICHTFUEHRER and user.empleado_id:
        base = base | _permisos_por_sustitucion(user.empleado_id, db)

    return base


def _permisos_por_sustitucion(empleado_id, db: "Session") -> frozenset[str]:
    """
    Retorna PERMISOS_DELEGADOS_SCHICHTFUEHRER si hay algún Schichtführer
    que tenga este empleado como stellvertreter y esté ausente hoy.
    """
    from .models.empleado import Empleado
    from .models.vacaciones import SolicitudVacaciones, EstadoSolicitud

    hoy = date.today()
    ausente = (
        db.query(SolicitudVacaciones)
        .join(Empleado, SolicitudVacaciones.empleado_id == Empleado.id)
        .filter(
            Empleado.stellvertreter_id == empleado_id,
            SolicitudVacaciones.estado == EstadoSolicitud.APROBADA,
            SolicitudVacaciones.fecha_inicio <= hoy,
            SolicitudVacaciones.fecha_fin >= hoy,
        )
        .first()
    )
    return PERMISOS_DELEGADOS_SCHICHTFUEHRER if ausente else frozenset()
