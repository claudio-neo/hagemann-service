"""
Granular permission definitions for the Hagemann system.

Source of truth: Stammdaten sheet in importvorlage-hagemann.xlsx.
  Admin              — Unrestricted access
  Schichtführer      — Hour release, hour control, team leave overview,
                       assign deputy
  Stv. Schichtführer — Same as Benutzer; inherits Schichtführer permissions
                       automatically while substituting an absent shift lead
  Benutzer           — Clock in/out, smoke break, leave requests, own hours/leave
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

# ── Permission constants ──────────────────────────────────────────────────────

# Time clock — own
TIMECLOCK_REGISTER = "timeclock:register"   # clock in / out / smoke break / FZA
TIMECLOCK_VIEW_OWN = "timeclock:view_own"

# Hours — Schichtführer level
HOURS_RELEASE_TEAM = "hours:release_team"   # Stundenfreigabe
HOURS_CONTROL_TEAM = "hours:control_team"   # Stundenkontrolle

# Leave
LEAVE_REQUEST  = "leave:request"            # Urlaubsantrag stellen
LEAVE_VIEW_OWN = "leave:view_own"
LEAVE_VIEW_TEAM = "leave:view_team"         # Urlaubsübersicht seines Teams
LEAVE_APPROVE  = "leave:approve"            # Level 1 approval (Schichtführer)

# Deputy management
DEPUTY_ASSIGN = "deputy:assign"             # Schichtführer assigns their substitute

# Corrections and approvals
CORRECTIONS_REVIEW = "corrections:review"
APPROVALS_LEVEL1   = "approvals:level1"
APPROVALS_LEVEL2   = "approvals:level2"

# Reports and export
REPORTS_VIEW = "reports:view"
EXPORT_RUN   = "export:run"

# Administration
USERS_ADMIN    = "users:admin"
SHIFTS_WRITE   = "shifts:write"
EMPLOYEES_EDIT = "employees:edit"


# ── Role → base permission sets ───────────────────────────────────────────────

_BASE_USER: frozenset[str] = frozenset({
    TIMECLOCK_REGISTER,
    TIMECLOCK_VIEW_OWN,
    LEAVE_REQUEST,
    LEAVE_VIEW_OWN,
})

_BASE_SHIFT_LEAD: frozenset[str] = _BASE_USER | frozenset({
    HOURS_RELEASE_TEAM,
    HOURS_CONTROL_TEAM,
    LEAVE_VIEW_TEAM,
    LEAVE_APPROVE,
    CORRECTIONS_REVIEW,
    APPROVALS_LEVEL1,
    REPORTS_VIEW,
    DEPUTY_ASSIGN,
})

_BASE_ADMIN: frozenset[str] = _BASE_SHIFT_LEAD | frozenset({
    APPROVALS_LEVEL2,
    EXPORT_RUN,
    USERS_ADMIN,
    SHIFTS_WRITE,
    EMPLOYEES_EDIT,
})

PERMISSIONS_BY_ROLE: dict[int, frozenset[str]] = {
    ROLE_ADMIN:              _BASE_ADMIN,
    ROLE_SCHICHTFUEHRER:     _BASE_SHIFT_LEAD,
    ROLE_STV_SCHICHTFUEHRER: _BASE_USER,    # expanded dynamically when substituting
    ROLE_BENUTZER:           _BASE_USER,
}

# Permissions granted to the deputy (Stv. Schichtführer) while the shift lead is absent
DEPUTY_SUBSTITUTION_PERMISSIONS: frozenset[str] = frozenset({
    HOURS_RELEASE_TEAM,
    HOURS_CONTROL_TEAM,
    LEAVE_VIEW_TEAM,
    LEAVE_APPROVE,
    CORRECTIONS_REVIEW,
    APPROVALS_LEVEL1,
    REPORTS_VIEW,
})


# ── Effective permissions (resolves automatic delegation) ─────────────────────

def effective_permissions(user: "Usuario", db: "Session") -> frozenset[str]:
    """
    Returns the full permission set for a user, including delegated permissions
    if the user is a Stv. Schichtführer currently substituting an absent shift lead.
    """
    base = PERMISSIONS_BY_ROLE.get(user.role, frozenset())

    if user.role == ROLE_STV_SCHICHTFUEHRER and user.empleado_id:
        base = base | _substitution_permissions(user.empleado_id, db)

    return base


def _substitution_permissions(employee_id, db: "Session") -> frozenset[str]:
    """
    Returns DEPUTY_SUBSTITUTION_PERMISSIONS if any Schichtführer who has this
    employee as their deputy currently has an approved absence covering today.
    """
    from .models.empleado import Empleado
    from .models.vacaciones import SolicitudVacaciones, EstadoSolicitud

    today = date.today()
    absent = (
        db.query(SolicitudVacaciones)
        .join(Empleado, SolicitudVacaciones.empleado_id == Empleado.id)
        .filter(
            Empleado.stellvertreter_id == employee_id,
            SolicitudVacaciones.estado == EstadoSolicitud.APROBADA,
            SolicitudVacaciones.fecha_inicio <= today,
            SolicitudVacaciones.fecha_fin >= today,
        )
        .first()
    )
    return DEPUTY_SUBSTITUTION_PERMISSIONS if absent else frozenset()
