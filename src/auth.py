"""
Autenticación JWT y control de roles (HG-13)
Proporciona:
  - create_access_token(data) → str
  - get_current_user(token) → Usuario (dependency)
  - require_role(min_role) → dependency factory
"""
from datetime import datetime, timedelta
from typing import Optional

import bcrypt as _bcrypt

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models.usuario import Usuario

settings = get_settings()

# ── Contraseñas ──────────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Decodifica y valida un JWT. Lanza HTTPException si es inválido/expirado."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido o expirado: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Bearer extractor ─────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=True)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Usuario:
    """
    Dependency que extrae y valida el token Bearer, devolviendo el Usuario activo.
    Uso:  current_user: Usuario = Depends(get_current_user)
    """
    payload = decode_token(credentials.credentials)
    nick: str = payload.get("sub")
    if not nick:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token sin sub")

    user = db.query(Usuario).filter(Usuario.nick == nick).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario no encontrado")
    if not user.activo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Usuario inactivo")
    return user


def require_role(min_role: int):
    """
    Factory que devuelve una dependency que exige role <= min_role.
    Roles: 1=Admin (más privilegiado), 2=Abteilungsleiter, 3=Mitarbeiter
    Un Admin (1) puede todo; Abteilungsleiter (2) puede lo suyo y lo de Mitarbeiter.
    """
    def _check(current_user: Usuario = Depends(get_current_user)) -> Usuario:
        if current_user.role > min_role:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Se requiere rol ≤ {min_role} (tienes {current_user.role})",
            )
        return current_user
    return _check
