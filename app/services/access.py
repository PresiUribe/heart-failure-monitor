import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.patient_assignment import PatientAssignment
from app.models.user import User
from app.services.roles import canonical_role


CLINICAL_ROLES = {"medico", "especialista"}


def user_can_access_patient(
    db: Session,
    *,
    user: User,
    patient_id: uuid.UUID,
) -> bool:
    if canonical_role(user.role.code) == "admin":
        return True

    assignment_id = db.scalar(
        select(PatientAssignment.id).where(
            PatientAssignment.patient_id == patient_id,
            PatientAssignment.user_id == user.id,
            PatientAssignment.active.is_(True),
        )
    )

    return assignment_id is not None


def ensure_patient_access(
    db: Session,
    *,
    user: User,
    patient_id: uuid.UUID,
) -> None:
    if not user_can_access_patient(db, user=user, patient_id=patient_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene acceso a este paciente",
        )


def can_modify_owned_clinical_record(
    *,
    user: User,
    created_by: uuid.UUID,
) -> bool:
    if canonical_role(user.role.code) == "admin":
        return True
    return canonical_role(user.role.code) in CLINICAL_ROLES and user.id == created_by


def ensure_can_modify_owned_clinical_record(
    *,
    user: User,
    created_by: uuid.UUID,
) -> None:
    if not can_modify_owned_clinical_record(user=user, created_by=created_by):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Solo ADMIN o el profesional clínico que creó el registro "
                "puede modificarlo o eliminarlo."
            ),
        )
