# Importar todos los modelos para que SQLAlchemy los registre
from .empleado import Empleado, Grupo, CentroCoste, Zeitgruppe
from .fichaje import Fichaje, SegmentoTiempo, FuenteFichaje
from .vacaciones import (
    Festivo, PeriodoVacaciones, SolicitudVacaciones, LimiteVacaciones,
    TipoFestivo, TipoAusencia, EstadoSolicitud,
)
from .saldo_horas import SaldoHorasMensual

# HG-13
from .usuario import Usuario

# HG-14 + HG-15
from .turno import ModeloTurno, PlanTurno

# HG-17
from .aprobacion import AprobacionLog

# HG-16
from .correccion import SolicitudCorreccion

# HG-12 DATEV
from .datev import DatevConfig, DatevExportLog

# Audit Log
from .audit import AuditLog, InteractionLog

# Pausen (Raucherpause etc.)
from .pausa import Pausa

__all__ = [
    # Empleados
    "Empleado", "Grupo", "CentroCoste",
    # Fichajes
    "Fichaje", "SegmentoTiempo", "FuenteFichaje",
    # Vacaciones
    "Festivo", "PeriodoVacaciones", "SolicitudVacaciones", "LimiteVacaciones",
    "TipoFestivo", "TipoAusencia", "EstadoSolicitud",
    # Saldo horas
    "SaldoHorasMensual",
    # HG-13 Usuarios
    "Usuario",
    # HG-14/15 Turnos
    "ModeloTurno", "PlanTurno",
    # HG-17 Aprobaciones
    "AprobacionLog",
    # HG-16 Correcciones
    "SolicitudCorreccion",
    # HG-12 DATEV
    "DatevConfig",
    "DatevExportLog",
    # Zeitgruppe
    "Zeitgruppe",
    # Audit Log
    "AuditLog",
    "InteractionLog",
]
