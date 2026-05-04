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
from ..models.usuario import Usuario, ROLE_LABELS
from ..auth import (
    verify_password, hash_password,
    create_access_token, decode_token,
    get_current_user,
)
from ..permisos import effective_permissions, DEPUTY_SUBSTITUTION_PERMISSIONS
from ..models.usuario import ROLE_STV_SCHICHTFUEHRER

router = APIRouter(prefix="/auth", tags=["Autenticación"])


# ─── Schemas ─────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: int
    role_name: str
    nick: str

class RefreshRequest(BaseModel):
    token: str


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _user_dict(u: Usuario, db) -> dict:
    effective = effective_permissions(u, db)
    substituting = (
        u.role == ROLE_STV_SCHICHTFUEHRER
        and bool(DEPUTY_SUBSTITUTION_PERMISSIONS & effective)
    )
    return {
        "id": str(u.id),
        "nick": u.nick,
        "email": u.email,
        "role": u.role,
        "role_name": ROLE_LABELS.get(u.role, "Unbekannt"),
        "employee_id": str(u.empleado_id) if u.empleado_id else None,
        "active": u.activo,
        "last_login": u.last_login.isoformat() + "Z" if u.last_login else None,
        "permissions": sorted(effective),
        "substituting": substituting,
    }


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
            "Ungültige Anmeldedaten",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user.last_login = datetime.utcnow()
    db.commit()

    token = create_access_token({"sub": user.nick, "role": user.role})
    return TokenResponse(
        access_token=token,
        role=user.role,
        role_name=ROLE_LABELS.get(user.role, "Unbekannt"),
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
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültiger Benutzer")

    new_token = create_access_token({"sub": user.nick, "role": user.role})
    return {"access_token": new_token, "token_type": "bearer"}


@router.get("/me")
def get_me(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Datos del usuario autenticado + permisos efectivos."""
    return _user_dict(current_user, db)


# ─── Seed ────────────────────────────────────────────────────────────────────

def seed_usuarios(db: Session):
    """Crea los usuarios de demo si no existen. Idempotente."""
    from ..models.usuario import ROLE_ADMIN, ROLE_SCHICHTFUEHRER, ROLE_STV_SCHICHTFUEHRER, ROLE_BENUTZER
    seeds = [
        {"nick": "admin",    "email": "admin@hagemann.de",  "password": "admin123",  "role": ROLE_ADMIN},
        {"nick": "schicht1", "email": "schicht1@hagemann.de","password": "schicht123","role": ROLE_SCHICHTFUEHRER},
        {"nick": "stv1",     "email": "stv1@hagemann.de",   "password": "stv123",    "role": ROLE_STV_SCHICHTFUEHRER},
        {"nick": "emp1",     "email": "emp1@hagemann.de",   "password": "emp123",    "role": ROLE_BENUTZER},
    ]
    for s in seeds:
        exists = db.query(Usuario).filter(
            (Usuario.nick == s["nick"]) | (Usuario.email == s["email"])
        ).first()
        if not exists:
            db.add(Usuario(
                nick=s["nick"],
                email=s["email"],
                password_hash=hash_password(s["password"]),
                role=s["role"],
            ))
    db.commit()
