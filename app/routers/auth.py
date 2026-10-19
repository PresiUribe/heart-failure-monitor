from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.schemas.auth import TokenResponse
from app.security.jwt import create_access_token
from app.security.password import verify_password
from app.services.audit import add_audit
from app.services.notifications import admin_ids, create_notification, publish_notification

router = APIRouter(prefix="/auth", tags=["Autenticación"])


class _Credentials:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password


async def read_credentials(request: Request) -> _Credentials:
    """Acepta form-urlencoded (Swagger/OAuth2) y JSON (tester, Postman, SPA)."""
    content_type = request.headers.get("content-type", "")
    data: dict = {}
    try:
        if "application/json" in content_type:
            body = await request.json()
            data = body if isinstance(body, dict) else {}
        else:
            data = dict(await request.form())
    except Exception:
        data = {}
    username = str(data.get("username") or data.get("usuario") or data.get("user") or "").strip()
    password = str(data.get("password") or data.get("contrasena") or data.get("clave") or "")
    if not username or not password:
        raise HTTPException(status_code=422, detail="Se requieren username y password")
    return _Credentials(username, password)


@router.post("/login", response_model=TokenResponse)
async def login(form_data: _Credentials = Depends(read_credentials), db: Session = Depends(get_db)):
    username = form_data.username.strip()
    user = db.scalar(select(User).where(User.username == username, User.is_active.is_(True), User.deleted_at.is_(None)))

    if user is None:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos", headers={"WWW-Authenticate":"Bearer"})

    if user.is_locked:
        raise HTTPException(status_code=423, detail="Cuenta bloqueada. Solo un administrador puede desbloquearla.")

    if not verify_password(form_data.password, user.password_hash):
        user.failed_login_attempts = int(user.failed_login_attempts or 0) + 1
        add_audit(db, user_id=user.id, action="LOGIN_FAILED", entity_type="USER", entity_id=user.id, details={"attempt": user.failed_login_attempts})
        pending_notifications = []
        if user.failed_login_attempts >= 3:
            user.is_locked = True
            user.locked_at = datetime.utcnow()
            add_audit(db, user_id=user.id, action="ACCOUNT_LOCKED", entity_type="USER", entity_id=user.id, details={"reason":"3 consecutive failed passwords"})
            for admin_id in admin_ids(db):
                pending_notifications.append(create_notification(db, user_id=admin_id, tipo="cuenta_bloqueada", mensaje=f"La cuenta {user.username} fue bloqueada tras 3 intentos fallidos.", data={"usuario_id":str(user.id), "username":user.username}))
        db.commit()
        for n in pending_notifications:
            db.refresh(n)
            await publish_notification(n)
        if user.is_locked:
            raise HTTPException(status_code=423, detail="Cuenta bloqueada después de 3 intentos fallidos.")
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos", headers={"WWW-Authenticate":"Bearer"})

    user.failed_login_attempts = 0
    user.locked_at = None
    add_audit(db, user_id=user.id, action="LOGIN_SUCCESS", entity_type="USER", entity_id=user.id)
    db.commit()
    token = create_access_token(user_id=str(user.id), role_code=user.role.code, username=user.username, document_number=user.document_number)
    return TokenResponse(access_token=token)
