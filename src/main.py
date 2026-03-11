import sys
from pathlib import Path

# Add shared module to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "shared"))

from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .config import get_settings
from .database import engine, Base, SessionLocal, init_schema

# Importar modelos para que SQLAlchemy los registre
from .models import (
    Empleado, Grupo, CentroCoste, Fichaje, SegmentoTiempo,
    Festivo, PeriodoVacaciones, SolicitudVacaciones, LimiteVacaciones,
    SaldoHorasMensual,
    # HG-13
    Usuario,
    # HG-14 + HG-15
    ModeloTurno, PlanTurno,
    # HG-17
    AprobacionLog,
    # HG-16
    SolicitudCorreccion,
    # HG-12 DATEV
    DatevConfig,
    DatevExportLog,
)

# Importar rutas
from .routes import (
    health, empleados, grupos, centros_coste, fichajes, reportes,
    vacaciones, festivos, saldo_horas,
)
from .routes.auth import router as auth_router, seed_usuarios
from .routes.turnos import router as turnos_router, seed_modelos_turno
from .routes.aprobaciones import router as aprobaciones_router
from .routes.correcciones import router as correcciones_router
from .routes.exportacion import router as exportacion_router
from .routes.datev import router as datev_router

settings = get_settings()

# Crear schema y tablas
init_schema()
Base.metadata.create_all(bind=engine)

# Seed inicial (idempotente)
def _run_seeds():
    db = SessionLocal()
    try:
        seed_usuarios(db)
        seed_modelos_turno(db)
    finally:
        db.close()

_run_seeds()

app = FastAPI(
    title="Hagemann — Sistema de Control Horario",
    description="""
## API de Control Horario para Hagemann

Demo de sistema de fichaje con asignación por centro de coste / departamento.

### Módulos

- **Empleados**: Gestión de personal (compatible con nfc2 de RTR)
- **Centros de Coste**: Departamentos para imputación de horas
- **Fichajes**: Entrada / Salida / Cambio de departamento
- **Reportes**: Doble vista empleado↔departamento
- **Autenticación**: JWT + Roles (HG-13)
- **Turnos**: Modelos y planificación de turnos (HG-14/15)
- **Aprobaciones**: Sistema 2 niveles (HG-17)
- **Correcciones**: Solicitudes de corrección de fichaje (HG-16)

### Flujo de Fichaje

1. Empleado llega → ficha ENTRADA seleccionando departamento
2. Si cambia de departamento → CAMBIO (cierra segmento, abre nuevo)
3. Al irse → ficha SALIDA (cierra todo, calcula totales)
    """,
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rutas existentes ──────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(empleados.router, prefix="/api/v1")
app.include_router(grupos.router, prefix="/api/v1")
app.include_router(centros_coste.router, prefix="/api/v1")
app.include_router(fichajes.router, prefix="/api/v1")
app.include_router(reportes.router, prefix="/api/v1")
app.include_router(festivos.router, prefix="/api/v1")
app.include_router(vacaciones.router, prefix="/api/v1")
app.include_router(saldo_horas.router, prefix="/api/v1")

# ── Rutas nuevas (HG-13 a HG-17) ─────────────────────────────────────────────
app.include_router(auth_router, prefix="/api/v1")
app.include_router(turnos_router, prefix="/api/v1")
app.include_router(aprobaciones_router, prefix="/api/v1")
app.include_router(correcciones_router, prefix="/api/v1")
app.include_router(exportacion_router, prefix="/api/v1")
app.include_router(datev_router, prefix="/api/v1")


@app.get("/api/info")
def api_info():
    return {
        "service": settings.service_name,
        "version": "0.2.0",
        "modules": [
            "empleados", "grupos", "centros_coste",
            "fichajes", "reportes",
            "festivos", "vacaciones", "saldo_horas",
            "auth", "turnos", "aprobaciones", "correcciones", "exportacion", "datev",
        ],
        "docs": "/docs",
    }

# Static files (UI)
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

@app.get("/")
def root():
    index = _static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index), media_type="text/html")
    return {"service": settings.service_name, "docs": "/docs"}
