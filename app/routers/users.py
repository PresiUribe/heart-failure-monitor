from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user, require_roles
from app.models.role import Role
from app.models.user import User
from app.schemas.user import AdminPasswordReset, PasswordResetResponse, UserCreate, UserResponse, UserUpdate
from app.security.password import hash_password
from app.services.audit import add_audit, add_history, model_snapshot
from app.services.identifiers import resolve_user_identifier

router = APIRouter(prefix='/users', tags=['Usuarios'])


@router.get('/me', response_model=UserResponse)
def read_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get('', response_model=list[UserResponse])
def list_users(include_deleted: bool = Query(default=False), db: Session = Depends(get_db), _: User = Depends(require_roles('ADMIN'))):
    query = select(User)
    if not include_deleted:
        query = query.where(User.is_active.is_(True), User.deleted_at.is_(None))
    return list(db.scalars(query.order_by(User.user_code.asc())))


@router.post('', response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db), current_user: User = Depends(require_roles('ADMIN'))):
    existing_user = db.scalar(select(User).where(or_(
        User.username == payload.username,
        User.email == str(payload.email).lower(),
        User.document_number == payload.document_number,
    )))
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='El usuario, email o documento ya esta registrado')

    role = db.scalar(select(Role).where(Role.code == payload.role_code))
    if role is None:
        raise HTTPException(status_code=400, detail='Rol no valido')

    user = User(
        document_type=payload.document_type,
        document_number=payload.document_number,
        full_name=payload.full_name,
        username=payload.username,
        email=str(payload.email).lower(),
        password_hash=hash_password(payload.password),
        role_id=role.id,
    )
    db.add(user); db.flush()
    add_audit(db, user_id=current_user.id, action='CREATE', entity_type='USER', entity_id=user.id, details={
        'user_code': user.user_code, 'document_number': user.document_number, 'username': user.username, 'role': role.code
    })
    db.commit(); db.refresh(user)
    return user


@router.get('/{user_identifier}', response_model=UserResponse)
def get_user_by_identifier(
    user_identifier: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles('ADMIN')),
):
    """Consulta un usuario por cedula/documento. UUID y user_code siguen soportados."""
    return resolve_user_identifier(db, user_identifier, include_inactive=True)


@router.put('/{user_identifier}', response_model=UserResponse)
def update_user(user_identifier: str, payload: UserUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_roles('ADMIN'))):
    user = resolve_user_identifier(db, user_identifier, include_inactive=True)
    previous = model_snapshot(user)
    updates = payload.model_dump(exclude_unset=True)

    if 'document_type' in updates and updates['document_type'] is not None:
        user.document_type = updates.pop('document_type')
    if 'document_number' in updates and updates['document_number'] is not None:
        document_number = updates.pop('document_number').strip()
        duplicate = db.scalar(select(User).where(User.document_number == document_number, User.id != user.id))
        if duplicate:
            raise HTTPException(status_code=409, detail='El documento ya esta registrado')
        user.document_number = document_number
    if 'full_name' in updates:
        user.full_name = updates.pop('full_name')
    if 'email' in updates and updates['email'] is not None:
        email = str(updates.pop('email')).lower()
        duplicate = db.scalar(select(User).where(User.email == email, User.id != user.id))
        if duplicate:
            raise HTTPException(status_code=409, detail='El email ya esta registrado')
        user.email = email
    if 'role_code' in updates:
        role_code = updates.pop('role_code')
        if role_code is not None:
            role = db.scalar(select(Role).where(Role.code == role_code))
            if role is None:
                raise HTTPException(status_code=400, detail='Rol no valido')
            user.role_id = role.id
    if 'is_active' in updates and updates['is_active'] is not None:
        user.is_active = updates['is_active']

    user.updated_at = datetime.utcnow()
    add_history(db, entity_type='USER', entity_id=user.id, previous_data=previous, new_data=model_snapshot(user), changed_by=current_user.id, change_type='SOFT_EDIT')
    add_audit(db, user_id=current_user.id, action='SOFT_EDIT', entity_type='USER', entity_id=user.id, details={'document_number': user.document_number})
    db.commit(); db.refresh(user)
    return user


@router.post('/{user_identifier}/reset-password', response_model=PasswordResetResponse)
def reset_user_password(user_identifier: str, payload: AdminPasswordReset, db: Session = Depends(get_db), current_user: User = Depends(require_roles('ADMIN'))):
    user = resolve_user_identifier(db, user_identifier, include_inactive=True)
    if not user.is_active or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Restaure el usuario antes de cambiar su contrasena')
    user.password_hash = hash_password(payload.new_password); user.updated_at = datetime.utcnow()
    add_audit(db, user_id=current_user.id, action='PASSWORD_RESET', entity_type='USER', entity_id=user.id, details={
        'target_document_number': user.document_number, 'target_user_code': user.user_code, 'target_username': user.username, 'performed_by': current_user.user_code
    })
    db.commit(); db.refresh(user)
    return PasswordResetResponse(user_id=user.id, document_number=user.document_number, user_code=user.user_code, username=user.username, message='Contrasena restablecida correctamente')


@router.delete('/{user_identifier}', response_model=UserResponse)
def soft_delete_user(user_identifier: str, db: Session = Depends(get_db), current_user: User = Depends(require_roles('ADMIN'))):
    user = resolve_user_identifier(db, user_identifier, include_inactive=True)
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail='El administrador autenticado no puede eliminarse a si mismo')
    previous = model_snapshot(user)
    user.is_active = False; user.deleted_at = datetime.utcnow(); user.deleted_by = current_user.id; user.updated_at = datetime.utcnow()
    add_history(db, entity_type='USER', entity_id=user.id, previous_data=previous, new_data=model_snapshot(user), changed_by=current_user.id, change_type='SOFT_DELETE')
    add_audit(db, user_id=current_user.id, action='SOFT_DELETE', entity_type='USER', entity_id=user.id)
    db.commit(); db.refresh(user)
    return user


@router.post('/{user_identifier}/restore', response_model=UserResponse)
def restore_user(user_identifier: str, db: Session = Depends(get_db), current_user: User = Depends(require_roles('ADMIN'))):
    user = resolve_user_identifier(db, user_identifier, include_inactive=True)
    if user.is_active and user.deleted_at is None:
        raise HTTPException(status_code=409, detail='El usuario ya esta activo')
    previous = model_snapshot(user)
    user.is_active = True; user.deleted_at = None; user.deleted_by = None; user.updated_at = datetime.utcnow()
    add_history(db, entity_type='USER', entity_id=user.id, previous_data=previous, new_data=model_snapshot(user), changed_by=current_user.id, change_type='RESTORE')
    add_audit(db, user_id=current_user.id, action='RESTORE', entity_type='USER', entity_id=user.id)
    db.commit(); db.refresh(user)
    return user


@router.post('/{user_identifier}/unlock')
async def unlock_user(user_identifier: str, db: Session = Depends(get_db), current_user: User = Depends(require_roles('ADMIN'))):
    user = resolve_user_identifier(db, user_identifier, include_inactive=True)
    user.is_locked = False; user.failed_login_attempts = 0; user.locked_at = None; user.updated_at = datetime.utcnow()
    add_audit(db, user_id=current_user.id, action='ACCOUNT_UNLOCKED', entity_type='USER', entity_id=user.id, details={
        'target_username': user.username, 'target_document_number': user.document_number
    })
    db.commit()
    return {'id': str(user.id), 'document_number': user.document_number, 'username': user.username, 'is_locked': False}
