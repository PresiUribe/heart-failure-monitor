import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.identifiers import resolve_patient_identifier
from app.dependencies.auth import get_current_user, require_roles
from app.models.encounter import Encounter
from app.models.observation import Observation
from app.models.observation_component import ObservationComponent
from app.models.patient import Patient
from app.models.user import User
from app.schemas.observation import (
    BloodPressureCreate,
    ObservationCreate,
    ObservationResponse,
    ObservationUpdate,
)
from app.services.access import (
    ensure_can_modify_owned_clinical_record,
    ensure_patient_access,
)
from app.services.audit import add_audit, add_history, model_snapshot

router = APIRouter(tags=["Observaciones"])


def _get_observation_any_state(db: Session, observation_id: uuid.UUID) -> Observation:
    observation = db.get(Observation, observation_id)
    if observation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observación no encontrada",
        )
    return observation


def _get_active_observation(db: Session, observation_id: uuid.UUID) -> Observation:
    observation = db.scalar(
        select(Observation).where(
            Observation.id == observation_id,
            Observation.is_active.is_(True),
            Observation.deleted_at.is_(None),
        )
    )
    if observation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observación no encontrada o inactiva",
        )
    return observation


def _validate_encounter(
    db: Session,
    *,
    encounter_id: uuid.UUID | None,
    patient_id: uuid.UUID,
) -> None:
    if encounter_id is None:
        return
    encounter = db.scalar(
        select(Encounter).where(
            Encounter.id == encounter_id,
            Encounter.patient_id == patient_id,
            Encounter.is_active.is_(True),
            Encounter.deleted_at.is_(None),
        )
    )
    if encounter is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El encounter_id no pertenece al paciente o está inactivo",
        )


@router.post(
    "/patients/{patient_id}/observations",
    response_model=ObservationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_observation(
    patient_id: str,
    payload: ObservationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO", "ESPECIALISTA")),
):
    patient = resolve_patient_identifier(db, patient_id)

    ensure_patient_access(db, user=current_user, patient_id=patient.id)
    _validate_encounter(
        db,
        encounter_id=payload.encounter_id,
        patient_id=patient.id,
    )

    data = payload.model_dump(exclude={"components"}, exclude_none=True)
    observation = Observation(
        patient_id=patient.id,
        created_by=current_user.id,
        **data,
    )
    db.add(observation)
    db.flush()

    for component in payload.components:
        db.add(
            ObservationComponent(
                observation_id=observation.id,
                **component.model_dump(),
            )
        )

    add_audit(
        db,
        user_id=current_user.id,
        action="CREATE",
        entity_type="OBSERVATION",
        entity_id=observation.id,
        details={"code": observation.code, "display": observation.display},
    )

    db.commit()
    db.refresh(observation)
    return observation


@router.post(
    "/patients/{patient_id}/observations/blood-pressure",
    response_model=ObservationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_blood_pressure(
    patient_id: str,
    payload: BloodPressureCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO", "ESPECIALISTA")),
):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db, user=current_user, patient_id=patient.id)
    _validate_encounter(
        db,
        encounter_id=payload.encounter_id,
        patient_id=patient.id,
    )

    observation = Observation(
        patient_id=patient.id,
        encounter_id=payload.encounter_id,
        created_by=current_user.id,
        code_system="http://loinc.org",
        code="85354-9",
        display="Blood pressure panel",
        value_type="COMPONENTS",
        measured_at=payload.measured_at or datetime.utcnow(),
    )
    db.add(observation)
    db.flush()

    db.add_all(
        [
            ObservationComponent(
                observation_id=observation.id,
                code_system="http://loinc.org",
                code="8480-6",
                display="Systolic blood pressure",
                value_numeric=payload.systolic,
                unit="mmHg",
            ),
            ObservationComponent(
                observation_id=observation.id,
                code_system="http://loinc.org",
                code="8462-4",
                display="Diastolic blood pressure",
                value_numeric=payload.diastolic,
                unit="mmHg",
            ),
        ]
    )

    add_audit(
        db,
        user_id=current_user.id,
        action="CREATE",
        entity_type="OBSERVATION",
        entity_id=observation.id,
        details={
            "code": "85354-9",
            "systolic": float(payload.systolic),
            "diastolic": float(payload.diastolic),
        },
    )

    db.commit()
    db.refresh(observation)
    return observation


@router.get(
    "/patients/{patient_id}/observations",
    response_model=list[ObservationResponse],
)
def list_observations(
    patient_id: str,
    include_deleted: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO", "ESPECIALISTA")),
):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db, user=current_user, patient_id=patient.id)
    if include_deleted and current_user.role.code != "ADMIN":
        raise HTTPException(status_code=403, detail="Solo ADMIN puede consultar observaciones eliminadas")

    query = select(Observation).where(Observation.patient_id == patient.id)
    if not include_deleted:
        query = query.where(
            Observation.is_active.is_(True),
            Observation.deleted_at.is_(None),
        )
    return list(db.scalars(query.order_by(Observation.measured_at.desc())))


@router.get("/observations/{observation_id}", response_model=ObservationResponse)
def get_observation(
    observation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO", "ESPECIALISTA")),
):
    observation = _get_active_observation(db, observation_id)
    ensure_patient_access(db, user=current_user, patient_id=observation.patient_id)
    return observation


@router.put("/observations/{observation_id}", response_model=ObservationResponse)
def update_observation(
    observation_id: uuid.UUID,
    payload: ObservationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    observation = _get_active_observation(db, observation_id)
    ensure_patient_access(db, user=current_user, patient_id=observation.patient_id)
    ensure_can_modify_owned_clinical_record(
        user=current_user,
        created_by=observation.created_by,
    )

    if payload.encounter_id is not None:
        _validate_encounter(
            db,
            encounter_id=payload.encounter_id,
            patient_id=observation.patient_id,
        )

    previous = model_snapshot(observation)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(observation, field, value)

    observation.updated_at = datetime.utcnow()

    add_history(
        db,
        entity_type="OBSERVATION",
        entity_id=observation.id,
        previous_data=previous,
        new_data=model_snapshot(observation),
        changed_by=current_user.id,
        change_type="SOFT_EDIT",
    )
    add_audit(
        db,
        user_id=current_user.id,
        action="SOFT_EDIT",
        entity_type="OBSERVATION",
        entity_id=observation.id,
        details={"fields": list(updates.keys())},
    )

    db.commit()
    db.refresh(observation)
    return observation


@router.delete("/observations/{observation_id}", response_model=ObservationResponse)
def soft_delete_observation(
    observation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    observation = _get_active_observation(db, observation_id)
    ensure_patient_access(db, user=current_user, patient_id=observation.patient_id)
    ensure_can_modify_owned_clinical_record(
        user=current_user,
        created_by=observation.created_by,
    )

    previous = model_snapshot(observation)
    observation.is_active = False
    observation.deleted_at = datetime.utcnow()
    observation.deleted_by = current_user.id
    observation.updated_at = datetime.utcnow()

    add_history(
        db,
        entity_type="OBSERVATION",
        entity_id=observation.id,
        previous_data=previous,
        new_data=model_snapshot(observation),
        changed_by=current_user.id,
        change_type="SOFT_DELETE",
    )
    add_audit(
        db,
        user_id=current_user.id,
        action="SOFT_DELETE",
        entity_type="OBSERVATION",
        entity_id=observation.id,
    )

    db.commit()
    db.refresh(observation)
    return observation


@router.post(
    "/observations/{observation_id}/restore",
    response_model=ObservationResponse,
)
def restore_observation(
    observation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role.code != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo ADMIN puede restaurar registros",
        )

    observation = _get_observation_any_state(db, observation_id)
    if observation.is_active and observation.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La observación ya está activa",
        )

    previous = model_snapshot(observation)
    observation.is_active = True
    observation.deleted_at = None
    observation.deleted_by = None
    observation.updated_at = datetime.utcnow()

    add_history(
        db,
        entity_type="OBSERVATION",
        entity_id=observation.id,
        previous_data=previous,
        new_data=model_snapshot(observation),
        changed_by=current_user.id,
        change_type="RESTORE",
    )
    add_audit(
        db,
        user_id=current_user.id,
        action="RESTORE",
        entity_type="OBSERVATION",
        entity_id=observation.id,
    )

    db.commit()
    db.refresh(observation)
    return observation
