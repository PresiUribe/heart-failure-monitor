import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user, require_roles
from app.models.encounter import Encounter
from app.models.observation import Observation
from app.models.patient import Patient
from app.models.user import User
from app.services.access import ensure_patient_access
from app.services.identifiers import resolve_patient_identifier
from app.services.fhir_client import FHIRClient, FHIRServiceError
from app.services.fhir_sync import (
    sync_encounter,
    sync_observation,
    sync_patient,
    sync_patient_all,
)

router = APIRouter(prefix="/fhir", tags=["FHIR R4"])


def _translate_error(exc: Exception):
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=str(exc),
    )


@router.get("/health")
def fhir_health(
    _: User = Depends(get_current_user),
):
    try:
        metadata = FHIRClient().metadata()
        return {
            "status": "ok",
            "fhirVersion": metadata.get("fhirVersion"),
            "software": metadata.get("software", {}),
        }
    except FHIRServiceError as exc:
        _translate_error(exc)


@router.post("/patients/{patient_id}/sync")
def sync_one_patient(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles("ADMIN", "MEDICO_RURAL", "ESPECIALISTA")
    ),
):
    patient = resolve_patient_identifier(db, patient_id)

    ensure_patient_access(db, user=current_user, patient_id=patient.id)

    try:
        return sync_patient(
            db,
            patient=patient,
            actor_id=current_user.id,
        )
    except (FHIRServiceError, RuntimeError, TypeError, ValueError) as exc:
        _translate_error(exc)


@router.post("/patients/{patient_id}/sync-all")
def sync_all_patient_resources(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles("ADMIN", "MEDICO_RURAL", "ESPECIALISTA")
    ),
):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db, user=current_user, patient_id=patient.id)

    try:
        return sync_patient_all(
            db,
            patient=patient,
            actor_id=current_user.id,
        )
    except (FHIRServiceError, RuntimeError, TypeError, ValueError) as exc:
        _translate_error(exc)


@router.get("/patients/{patient_id}")
def get_fhir_patient(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO_RURAL", "ESPECIALISTA")),
):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db, user=current_user, patient_id=patient.id)

    if not patient.fhir_patient_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El paciente aún no ha sido sincronizado con HAPI FHIR",
        )

    try:
        return FHIRClient().get("Patient", patient.fhir_patient_id)
    except FHIRServiceError as exc:
        _translate_error(exc)


@router.post("/encounters/{encounter_id}/sync")
def sync_one_encounter(
    encounter_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles("ADMIN", "MEDICO_RURAL", "ESPECIALISTA")
    ),
):
    encounter = db.get(Encounter, encounter_id)
    if encounter is None or not encounter.is_active:
        raise HTTPException(status_code=404, detail="Encuentro no encontrado")
    ensure_patient_access(db, user=current_user, patient_id=encounter.patient_id)

    try:
        return sync_encounter(
            db,
            encounter=encounter,
            actor_id=current_user.id,
        )
    except (FHIRServiceError, RuntimeError, TypeError, ValueError) as exc:
        _translate_error(exc)


@router.get("/encounters/{encounter_id}")
def get_fhir_encounter(
    encounter_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO_RURAL", "ESPECIALISTA")),
):
    encounter = db.get(Encounter, encounter_id)
    if encounter is None:
        raise HTTPException(status_code=404, detail="Encuentro no encontrado")
    ensure_patient_access(db, user=current_user, patient_id=encounter.patient_id)

    if not encounter.fhir_encounter_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El encuentro aún no ha sido sincronizado con HAPI FHIR",
        )
    try:
        return FHIRClient().get("Encounter", encounter.fhir_encounter_id)
    except FHIRServiceError as exc:
        _translate_error(exc)


@router.post("/observations/{observation_id}/sync")
def sync_one_observation(
    observation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles("ADMIN", "MEDICO_RURAL", "ESPECIALISTA")
    ),
):
    observation = db.get(Observation, observation_id)
    if observation is None or not observation.is_active:
        raise HTTPException(status_code=404, detail="Observación no encontrada")
    ensure_patient_access(db, user=current_user, patient_id=observation.patient_id)

    try:
        return sync_observation(
            db,
            observation=observation,
            actor_id=current_user.id,
        )
    except (FHIRServiceError, RuntimeError, TypeError, ValueError) as exc:
        _translate_error(exc)


@router.get("/observations/{observation_id}")
def get_fhir_observation(
    observation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO_RURAL", "ESPECIALISTA")),
):
    observation = db.get(Observation, observation_id)
    if observation is None:
        raise HTTPException(status_code=404, detail="Observación no encontrada")
    ensure_patient_access(db, user=current_user, patient_id=observation.patient_id)

    if not observation.fhir_observation_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La observación aún no ha sido sincronizada con HAPI FHIR",
        )
    try:
        return FHIRClient().get(
            "Observation",
            observation.fhir_observation_id,
        )
    except FHIRServiceError as exc:
        _translate_error(exc)


@router.get("/patients/{patient_id}/observations")
def search_fhir_observations_for_patient(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO_RURAL", "ESPECIALISTA")),
):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db, user=current_user, patient_id=patient.id)

    if not patient.fhir_patient_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El paciente aún no ha sido sincronizado con HAPI FHIR",
        )

    try:
        return FHIRClient().search(
            "Observation",
            {"subject": f"Patient/{patient.fhir_patient_id}"},
        )
    except FHIRServiceError as exc:
        _translate_error(exc)
