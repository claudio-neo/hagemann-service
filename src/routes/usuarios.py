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
from ..models.usuario import Usuario, ROLE_LABELS, ROLE_GRUPPENADMIN
from ..models.empleado import Empleado, Grupo
from ..auth import require_permission, hash_password, get_current_user
from ..permisos import USERS_ADMIN
from ..services.audit_service import log_action, diff_changes

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
    grupo_id: Optional[UUID] = None
    activo: bool = True


class UsuarioUpdate(BaseModel):
    nick: Optional[str] = None
    email: Optional[str] = None
    role: Optional[int] = None
    empleado_id: Optional[UUID] = None
    grupo_id: Optional[UUID] = None
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
        "grupo_id": str(u.grupo_id) if u.grupo_id else None,
        "grupo_nombre": u.grupo.nombre if u.grupo else None,
        "activo": u.activo,
        "last_login": u.last_login.isoformat() + "Z" if u.last_login else None,
        "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
    }


# ── Endpoints ─────────────────────────────────────────────

@router.get("/")
def listar_usuarios(db: Session = Depends(get_db)):
    usuarios = (
        db.query(Usuario)
        .options(joinedload(Usuario.empleado), joinedload(Usuario.grupo))
        .order_by(Usuario.role, Usuario.nick)
        .all()
    )
    return {"data": [_to_dict(u) for u in usuarios], "total": len(usuarios)}


@router.get("/{usuario_id}")
def obtener_usuario(usuario_id: UUID, db: Session = Depends(get_db)):
    u = db.query(Usuario).options(joinedload(Usuario.empleado), joinedload(Usuario.grupo)).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    return _to_dict(u)


@router.post("/", status_code=201)
def crear_usuario(data: UsuarioCreate, db: Session = Depends(get_db),
                  current_user: Usuario = Depends(get_current_user)):
    if db.query(Usuario).filter(Usuario.nick == data.nick).first():
        raise HTTPException(409, f"Nick '{data.nick}' bereits vergeben")
    if data.email and db.query(Usuario).filter(Usuario.email == data.email).first():
        raise HTTPException(409, f"E-Mail '{data.email}' bereits vergeben")
    if data.role not in ROLE_LABELS:
        raise HTTPException(400, f"Ungültige Rolle: {data.role}")
    if data.empleado_id:
        if not db.query(Empleado).filter(Empleado.id == data.empleado_id).first():
            raise HTTPException(404, "Mitarbeiter nicht gefunden")
    grupo_id = data.grupo_id
    if data.role == ROLE_GRUPPENADMIN:
        if not grupo_id:
            raise HTTPException(400, "Gruppenadmin benötigt eine zugewiesene Gruppe")
        if not db.query(Grupo).filter(Grupo.id == grupo_id).first():
            raise HTTPException(404, "Gruppe nicht gefunden")
    else:
        grupo_id = None  # solo Gruppenadmin usa grupo_id

    u = Usuario(
        nick=data.nick,
        email=data.email,
        password_hash=hash_password(data.password),
        role=data.role,
        empleado_id=data.empleado_id,
        grupo_id=grupo_id,
        activo=data.activo,
    )
    db.add(u)
    db.flush()
    log_action(db, "CREATE", "usuario",
               entidad_id=str(u.id),
               entidad_label=u.nick,
               descripcion=f"Benutzer erstellt: {u.nick} (Rolle {ROLE_LABELS.get(u.role, u.role)})",
               usuario_nick=current_user.nick)
    db.commit()
    db.refresh(u)
    return {"id": str(u.id), "message": "Benutzer erstellt"}


@router.put("/{usuario_id}")
def actualizar_usuario(usuario_id: UUID, data: UsuarioUpdate, db: Session = Depends(get_db),
                       current_user: Usuario = Depends(get_current_user)):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")

    old_state = {"nick": u.nick, "email": u.email, "role": u.role,
                 "empleado_id": str(u.empleado_id) if u.empleado_id else None,
                 "grupo_id": str(u.grupo_id) if u.grupo_id else None,
                 "activo": u.activo}

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

    if data.grupo_id is not None:
        if not db.query(Grupo).filter(Grupo.id == data.grupo_id).first():
            raise HTTPException(404, "Gruppe nicht gefunden")
        u.grupo_id = data.grupo_id

    # Consistencia rol↔grupo: Gruppenadmin exige grupo; otros roles lo limpian.
    if u.role == ROLE_GRUPPENADMIN:
        if not u.grupo_id:
            raise HTTPException(400, "Gruppenadmin benötigt eine zugewiesene Gruppe")
    else:
        u.grupo_id = None

    if data.activo is not None:
        u.activo = data.activo

    u.updated_at = datetime.utcnow()
    new_state = {"nick": u.nick, "email": u.email, "role": u.role,
                 "empleado_id": str(u.empleado_id) if u.empleado_id else None,
                 "grupo_id": str(u.grupo_id) if u.grupo_id else None,
                 "activo": u.activo}
    log_action(db, "UPDATE", "usuario",
               entidad_id=str(u.id),
               entidad_label=u.nick,
               cambios=diff_changes(old_state, new_state),
               usuario_nick=current_user.nick)
    db.commit()
    db.refresh(u)
    return {"message": "Benutzer aktualisiert", "usuario": _to_dict(u)}


@router.post("/{usuario_id}/reset-password")
def reset_password(usuario_id: UUID, data: PasswordReset, db: Session = Depends(get_db),
                   current_user: Usuario = Depends(get_current_user)):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if len(data.password) < 6:
        raise HTTPException(400, "Passwort muss mindestens 6 Zeichen haben")
    u.password_hash = hash_password(data.password)
    u.updated_at = datetime.utcnow()
    log_action(db, "PASSWORD_RESET", "usuario",
               entidad_id=str(u.id),
               entidad_label=u.nick,
               descripcion=f"Passwort für '{u.nick}' zurückgesetzt",
               usuario_nick=current_user.nick)
    db.commit()
    return {"message": f"Passwort für '{u.nick}' zurückgesetzt"}


@router.delete("/{usuario_id}")
def desactivar_usuario(usuario_id: UUID, db: Session = Depends(get_db),
                       current_user: Usuario = Depends(get_current_user)):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    u.activo = False
    u.updated_at = datetime.utcnow()
    log_action(db, "DEACTIVATE", "usuario",
               entidad_id=str(u.id),
               entidad_label=u.nick,
               descripcion=f"Benutzer '{u.nick}' deaktiviert",
               usuario_nick=current_user.nick)
    db.commit()
    return {"message": f"Benutzer '{u.nick}' deaktiviert"}
