"""
Gestión de usuarios del sistema — CRUD completo.
Sólo accesible con permiso USERS_ADMIN (rol Admin).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import datetime

from ..database import get_db
from ..models.usuario import Usuario, ROLE_LABELS
from ..models.empleado import Empleado
from ..auth import require_permission, hash_password
from ..permisos import USERS_ADMIN

router = APIRouter(
    prefix="/usuarios",
    tags=["Usuarios"],
    dependencies=[Depends(require_permission(USERS_ADMIN))],
)


# ── Schemas ──────────────────────────────────────────────

class UsuarioCreate(BaseModel):
    nick: str
    email: Optional[str] = None
    password: str
    role: int = 4
    empleado_id: Optional[UUID] = None
    activo: bool = True


class UsuarioUpdate(BaseModel):
    nick: Optional[str] = None
    email: Optional[str] = None
    role: Optional[int] = None
    empleado_id: Optional[UUID] = None
    activo: Optional[bool] = None


class PasswordReset(BaseModel):
    password: str


# ── Helpers ──────────────────────────────────────────────

def _to_dict(u: Usuario) -> dict:
    return {
        "id": str(u.id),
        "nick": u.nick,
        "email": u.email,
        "role": u.role,
        "role_label": ROLE_LABELS.get(u.role, "?"),
        "empleado_id": str(u.empleado_id) if u.empleado_id else None,
        "empleado_nombre": (
            f"{u.empleado.nombre} {u.empleado.apellido or ''}".strip()
            if u.empleado else None
        ),
        "activo": u.activo,
        "last_login": u.last_login.isoformat() + "Z" if u.last_login else None,
        "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
    }


# ── Endpoints ─────────────────────────────────────────────

@router.get("/")
def listar_usuarios(db: Session = Depends(get_db)):
    usuarios = (
        db.query(Usuario)
        .options(joinedload(Usuario.empleado))
        .order_by(Usuario.role, Usuario.nick)
        .all()
    )
    return {"data": [_to_dict(u) for u in usuarios], "total": len(usuarios)}


@router.get("/{usuario_id}")
def obtener_usuario(usuario_id: UUID, db: Session = Depends(get_db)):
    u = db.query(Usuario).options(joinedload(Usuario.empleado)).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    return _to_dict(u)


@router.post("/", status_code=201)
def crear_usuario(data: UsuarioCreate, db: Session = Depends(get_db)):
    if db.query(Usuario).filter(Usuario.nick == data.nick).first():
        raise HTTPException(409, f"Nick '{data.nick}' bereits vergeben")
    if data.email and db.query(Usuario).filter(Usuario.email == data.email).first():
        raise HTTPException(409, f"E-Mail '{data.email}' bereits vergeben")
    if data.role not in ROLE_LABELS:
        raise HTTPException(400, f"Ungültige Rolle: {data.role}")
    if data.empleado_id:
        if not db.query(Empleado).filter(Empleado.id == data.empleado_id).first():
            raise HTTPException(404, "Mitarbeiter nicht gefunden")

    u = Usuario(
        nick=data.nick,
        email=data.email,
        password_hash=hash_password(data.password),
        role=data.role,
        empleado_id=data.empleado_id,
        activo=data.activo,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"id": str(u.id), "message": "Benutzer erstellt"}


@router.put("/{usuario_id}")
def actualizar_usuario(usuario_id: UUID, data: UsuarioUpdate, db: Session = Depends(get_db)):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")

    if data.nick is not None:
        conflict = db.query(Usuario).filter(Usuario.nick == data.nick, Usuario.id != usuario_id).first()
        if conflict:
            raise HTTPException(409, f"Nick '{data.nick}' bereits vergeben")
        u.nick = data.nick

    if data.email is not None:
        conflict = db.query(Usuario).filter(Usuario.email == data.email, Usuario.id != usuario_id).first()
        if conflict:
            raise HTTPException(409, f"E-Mail '{data.email}' bereits vergeben")
        u.email = data.email

    if data.role is not None:
        if data.role not in ROLE_LABELS:
            raise HTTPException(400, f"Ungültige Rolle: {data.role}")
        u.role = data.role

    if data.empleado_id is not None:
        if not db.query(Empleado).filter(Empleado.id == data.empleado_id).first():
            raise HTTPException(404, "Mitarbeiter nicht gefunden")
        u.empleado_id = data.empleado_id

    if data.activo is not None:
        u.activo = data.activo

    u.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(u)
    return {"message": "Benutzer aktualisiert", "usuario": _to_dict(u)}


@router.post("/{usuario_id}/reset-password")
def reset_password(usuario_id: UUID, data: PasswordReset, db: Session = Depends(get_db)):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if len(data.password) < 6:
        raise HTTPException(400, "Passwort muss mindestens 6 Zeichen haben")
    u.password_hash = hash_password(data.password)
    u.updated_at = datetime.utcnow()
    db.commit()
    return {"message": f"Passwort für '{u.nick}' zurückgesetzt"}


@router.delete("/{usuario_id}")
def desactivar_usuario(usuario_id: UUID, db: Session = Depends(get_db)):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    u.activo = False
    u.updated_at = datetime.utcnow()
    db.commit()
    return {"message": f"Benutzer '{u.nick}' deaktiviert"}
