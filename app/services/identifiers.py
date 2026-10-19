import uuid

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.patient import Patient
from app.models.user import User


def _as_uuid(value: str):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def resolve_user_identifier(
    db: Session,
    identifier: str,
    *,
    include_inactive: bool = True,
) -> User:
    """Resolve a user from document number first, with legacy fallbacks.

    Swagger/users may use the human document number (cedula). Internal code can
    continue sending UUIDs, user_code or username without breaking compatibility.
    """
    value = str(identifier).strip()
    conditions = [
        User.document_number == value,
        User.user_code == value,
        User.username == value,
    ]
    parsed = _as_uuid(value)
    if parsed is not None:
        conditions.append(User.id == parsed)

    query = select(User).where(or_(*conditions))
    if not include_inactive:
        query = query.where(User.is_active.is_(True), User.deleted_at.is_(None))
    user = db.scalar(query)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Usuario no encontrado')
    return user


def resolve_patient_identifier(
    db: Session,
    identifier: str,
    *,
    include_inactive: bool = False,
) -> Patient:
    """Resolve a patient by document number, patient_code or internal UUID."""
    value = str(identifier).strip()
    conditions = [
        Patient.document_number == value,
        Patient.patient_code == value,
    ]
    parsed = _as_uuid(value)
    if parsed is not None:
        conditions.append(Patient.id == parsed)

    query = select(Patient).where(or_(*conditions))
    if not include_inactive:
        query = query.where(Patient.is_active.is_(True), Patient.deleted_at.is_(None))
    patient = db.scalar(query)
    if patient is None:
        detail = 'Paciente no encontrado' if include_inactive else 'Paciente no encontrado o inactivo'
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return patient
