from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.role import Role
from app.models.user import User
from app.schemas.user import BootstrapAdminRequest, UserResponse
from app.security.password import hash_password

router = APIRouter(prefix="/setup", tags=["Setup"])


@router.post(
    "/bootstrap-admin",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def bootstrap_admin(
    payload: BootstrapAdminRequest,
    db: Session = Depends(get_db),
):
    user_count = db.scalar(select(func.count()).select_from(User))

    if user_count and user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El bootstrap ya fue utilizado. Cree usuarios desde /users.",
        )

    admin_role = db.scalar(select(Role).where(Role.code == "ADMIN"))
    if admin_role is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No existe el rol ADMIN. Verifique sql/schema.sql.",
        )

    user = User(
        document_type=payload.document_type,
        document_number=payload.document_number,
        full_name=payload.full_name,
        username=payload.username,
        email=str(payload.email).lower(),
        password_hash=hash_password(payload.password),
        role_id=admin_role.id,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user
