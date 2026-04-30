"""
Autenticación JWT y control de acceso granular — Hagemann
Proporciona:
  - create_access_token(data) → str
  - get_current_user(token) → Usuario (dependency)
  - require_permission(permiso) → dependency factory  ← principal
  - require_role(min_role) → dependency factory       ← legacy, mantener compatibilidad
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

# ── Passwords ────────────────────────────────────────────────────────────────

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
    """Decodes and validates a JWT. Raises HTTPException if invalid or expired."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Bearer extractor ─────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=True)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Usuario:
    """Extracts and validates the Bearer token, returning the active user."""
    payload = decode_token(credentials.credentials)
    nick: str = payload.get("sub")
    if not nick:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing subject")

    user = db.query(Usuario).filter(Usuario.nick == nick).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    if not user.activo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User account is inactive")
    return user


def require_role(min_role: int):
    """Legacy: exige role <= min_role. Usar require_permission() para código nuevo."""
    def _check(current_user: Usuario = Depends(get_current_user)) -> Usuario:
        if current_user.role > min_role:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Erforderliche Rolle ≤ {min_role} (Ihre Rolle: {current_user.role})",
            )
        return current_user
    return _check


def require_permission(permission: str):
    """
    Dependency factory that checks a granular permission.
    Resolves automatic delegation for Stv. Schichtführer.

    Usage:
        @router.post("/hours/release")
        def release(u = Depends(require_permission(HOURS_RELEASE_TEAM))):
            ...
    """
    from .permisos import effective_permissions

    def _check(
        current_user: Usuario = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> Usuario:
        effective = effective_permissions(current_user, db)
        if permission not in effective:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Fehlende Berechtigung: {permission}",
            )
        return current_user
    return _check
