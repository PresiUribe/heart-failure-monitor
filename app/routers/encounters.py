import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.identifiers import resolve_patient_identifier
from app.dependencies.auth import get_current_user, require_roles
from app.models.encounter import Encounter
from app.models.patient import Patient
from app.models.user import User
from app.schemas.encounter import EncounterCreate, EncounterResponse, EncounterUpdate
from app.services.access import (
    ensure_can_modify_owned_clinical_record,
    ensure_patient_access,
)
from app.services.audit import add_audit, add_history, model_snapshot

router = APIRouter(tags=["Encuentros"])


def _get_encounter_any_state(db: Session, encounter_id: uuid.UUID) -> Encounter:
    encounter = db.get(Encounter, encounter_id)
    if encounter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Encuentro no encontrado",
        )
    return encounter


def _get_active_encounter(db: Session, encounter_id: uuid.UUID) -> Encounter:
    encounter = db.scalar(
        select(Encounter).where(
            Encounter.id == encounter_id,
            Encounter.is_active.is_(True),
            Encounter.deleted_at.is_(None),
        )
    )
    if encounter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Encuentro no encontrado o inactivo",
        )
    return encounter


@router.post(
    "/patients/{patient_id}/encounters",
    response_model=EncounterResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_encounter(
    patient_id: str,
    payload: EncounterCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles("ADMIN", "MEDICO", "ESPECIALISTA")
    ),
):
    patient = resolve_patient_identifier(db, patient_id)

    ensure_patient_access(db, user=current_user, patient_id=patient.id)

    data = payload.model_dump(exclude_none=True)
    encounter = Encounter(
        patient_id=patient.id,
        created_by=current_user.id,
        **data,
    )

    db.add(encounter)
    db.flush()
    add_audit(
        db,
        user_id=current_user.id,
        action="CREATE",
        entity_type="ENCOUNTER",
        entity_id=encounter.id,
        details={"encounter_type": encounter.encounter_type},
    )
    db.commit()
    db.refresh(encounter)
    return encounter


@router.get(
    "/patients/{patient_id}/encounters",
    response_model=list[EncounterResponse],
)
def list_encounters(
    patient_id: str,
    include_deleted: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO", "ESPECIALISTA")),
):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db, user=current_user, patient_id=patient.id)
    if include_deleted and current_user.role.code != "ADMIN":
        raise HTTPException(status_code=403, detail="Solo ADMIN puede consultar encuentros eliminados")

    query = select(Encounter).where(Encounter.patient_id == patient.id)
    if not include_deleted:
        query = query.where(Encounter.is_active.is_(True), Encounter.deleted_at.is_(None))
    return list(db.scalars(query.order_by(Encounter.start_at.desc())))


@router.get("/encounters/{encounter_id}", response_model=EncounterResponse)
def get_encounter(
    encounter_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO", "ESPECIALISTA")),
):
    encounter = _get_active_encounter(db, encounter_id)
    ensure_patient_access(db, user=current_user, patient_id=encounter.patient_id)
    return encounter


@router.put("/encounters/{encounter_id}", response_model=EncounterResponse)
def update_encounter(
    encounter_id: uuid.UUID,
    payload: EncounterUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    encounter = _get_active_encounter(db, encounter_id)
    ensure_patient_access(db, user=current_user, patient_id=encounter.patient_id)
    ensure_can_modify_owned_clinical_record(
        user=current_user,
        created_by=encounter.created_by,
    )

    previous = model_snapshot(encounter)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(encounter, field, value)

    encounter.updated_at = datetime.utcnow()

    add_history(
        db,
        entity_type="ENCOUNTER",
        entity_id=encounter.id,
        previous_data=previous,
        new_data=model_snapshot(encounter),
        changed_by=current_user.id,
        change_type="SOFT_EDIT",
    )
    add_audit(
        db,
        user_id=current_user.id,
        action="SOFT_EDIT",
        entity_type="ENCOUNTER",
        entity_id=encounter.id,
        details={"fields": list(updates.keys())},
    )
    db.commit()
    db.refresh(encounter)
    return encounter


@router.delete("/encounters/{encounter_id}", response_model=EncounterResponse)
def soft_delete_encounter(
    encounter_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    encounter = _get_active_encounter(db, encounter_id)
    ensure_patient_access(db, user=current_user, patient_id=encounter.patient_id)
    ensure_can_modify_owned_clinical_record(
        user=current_user,
        created_by=encounter.created_by,
    )

    previous = model_snapshot(encounter)
    encounter.is_active = False
    encounter.deleted_at = datetime.utcnow()
    encounter.deleted_by = current_user.id
    encounter.updated_at = datetime.utcnow()

    add_history(
        db,
        entity_type="ENCOUNTER",
        entity_id=encounter.id,
        previous_data=previous,
        new_data=model_snapshot(encounter),
        changed_by=current_user.id,
        change_type="SOFT_DELETE",
    )
    add_audit(
        db,
        user_id=current_user.id,
        action="SOFT_DELETE",
        entity_type="ENCOUNTER",
        entity_id=encounter.id,
    )
    db.commit()
    db.refresh(encounter)
    return encounter


@router.post(
    "/encounters/{encounter_id}/restore",
    response_model=EncounterResponse,
)
def restore_encounter(
    encounter_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN")),
):
    encounter = _get_encounter_any_state(db, encounter_id)
    if encounter.is_active and encounter.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El encuentro ya está activo",
        )

    previous = model_snapshot(encounter)
    encounter.is_active = True
    encounter.deleted_at = None
    encounter.deleted_by = None
    encounter.updated_at = datetime.utcnow()

    add_history(
        db,
        entity_type="ENCOUNTER",
        entity_id=encounter.id,
        previous_data=previous,
        new_data=model_snapshot(encounter),
        changed_by=current_user.id,
        change_type="RESTORE",
    )
    add_audit(
        db,
        user_id=current_user.id,
        action="RESTORE",
        entity_type="ENCOUNTER",
        entity_id=encounter.id,
    )
    db.commit()
    db.refresh(encounter)
    return encounter
