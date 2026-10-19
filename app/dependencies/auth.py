import uuid
from collections.abc import Callable
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.security.jwt import decode_access_token
from app.services.roles import role_matches

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def user_from_token(db: Session, token: str) -> User:
    credentials_exception = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido o expirado", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_access_token(token)
        subject = payload.get("sub")
        if not subject:
            raise credentials_exception
        user_id = uuid.UUID(subject)
    except (jwt.InvalidTokenError, ValueError):
        raise credentials_exception
    user = db.scalar(select(User).where(User.id == user_id, User.is_active.is_(True), User.deleted_at.is_(None)))
    if user is None or user.is_locked:
        raise credentials_exception
    return user


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    return user_from_token(db, token)


def require_roles(*allowed_roles: str) -> Callable:
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if not role_matches(current_user.role.code, *allowed_roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Acceso denegado. Rol requerido: {', '.join(allowed_roles)}")
        return current_user
    return dependency
