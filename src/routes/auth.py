"""
Endpoints de autenticación (HG-13)
  POST /auth/login    — username + password → JWT
  POST /auth/refresh  — renovar token
  GET  /auth/me       — datos del usuario autenticado
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.usuario import Usuario
from ..auth import (
    verify_password, hash_password,
    create_access_token, decode_token,
    get_current_user,
)

router = APIRouter(prefix="/auth", tags=["Autenticación"])


# ─── Schemas ─────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: int
    nick: str

class RefreshRequest(BaseModel):
    token: str


# ─── Helpers ─────────────────────────────────────────────────────────────────

_ROLE_NAMES = {1: "Admin", 2: "Abteilungsleiter", 3: "Mitarbeiter"}


def _user_dict(u: Usuario) -> dict:
    return {
        "id": str(u.id),
        "nick": u.nick,
        "email": u.email,
        "role": u.role,
        "role_name": _ROLE_NAMES.get(u.role, "Desconocido"),
        "empleado_id": str(u.empleado_id) if u.empleado_id else None,
        "activo": u.activo,
        "last_login": u.last_login.isoformat() + "Z" if u.last_login else None,
        "permisos": _get_permisos(u.role),
    }


def _get_permisos(role: int) -> list[str]:
    permisos_base = ["fichajes:leer", "turnos:leer", "perfil:leer"]
    if role <= 2:  # Abteilungsleiter y Admin
        permisos_base += [
            "turnos:escribir", "correcciones:revisar",
            "aprobaciones:nivel1", "reportes:ver",
        ]
    if role == 1:  # Solo Admin
        permisos_base += [
            "usuarios:admin", "aprobaciones:nivel2",
            "fichajes:editar", "exportar",
        ]
    return permisos_base


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    """Autenticar con nick + password. Devuelve JWT."""
    user = db.query(Usuario).filter(
        Usuario.nick == data.username,
        Usuario.activo == True,
    ).first()

    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Credenciales inválidas",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Actualizar last_login
    user.last_login = datetime.utcnow()
    db.commit()

    token = create_access_token({"sub": user.nick, "role": user.role})
    return TokenResponse(
        access_token=token,
        role=user.role,
        nick=user.nick,
    )


@router.post("/refresh")
def refresh_token(data: RefreshRequest, db: Session = Depends(get_db)):
    """Renovar un token no expirado."""
    payload = decode_token(data.token)
    nick = payload.get("sub")
    user = db.query(Usuario).filter(
        Usuario.nick == nick, Usuario.activo == True
    ).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario no válido")

    new_token = create_access_token({"sub": user.nick, "role": user.role})
    return {"access_token": new_token, "token_type": "bearer"}


@router.get("/me")
def get_me(current_user: Usuario = Depends(get_current_user)):
    """Datos del usuario autenticado + permisos."""
    return _user_dict(current_user)


# ─── Seed ────────────────────────────────────────────────────────────────────

def seed_usuarios(db: Session):
    """
    Crea los usuarios iniciales si no existen.
    Llamar una vez al arrancar la app.
    """
    seeds = [
        {"nick": "admin",     "email": "admin@hagemann.de",    "password": "admin123",   "role": 1},
        {"nick": "jefe1",     "email": "jefe1@hagemann.de",    "password": "jefe123",    "role": 2},
        {"nick": "empleado1", "email": "emp1@hagemann.de",     "password": "emp123",     "role": 3},
    ]
    for s in seeds:
        exists = db.query(Usuario).filter(Usuario.nick == s["nick"]).first()
        if not exists:
            db.add(Usuario(
                nick=s["nick"],
                email=s["email"],
                password_hash=hash_password(s["password"]),
                role=s["role"],
            ))
    db.commit()
