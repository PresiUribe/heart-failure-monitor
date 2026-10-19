from fastapi import APIRouter, Depends
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.corte2 import MeResponse
from app.services.roles import canonical_role

router = APIRouter(tags=["Acceso"])

@router.get("/yo", response_model=MeResponse)
def yo(current_user: User = Depends(get_current_user)):
    return MeResponse(id=current_user.id, user_code=current_user.user_code, document_type=current_user.document_type, document_number=current_user.document_number, username=current_user.username, rol=canonical_role(current_user.role.code))
