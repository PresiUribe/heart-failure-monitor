import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.encounter import Encounter
from app.models.observation import Observation
from app.models.patient import Patient
from app.services.audit import add_audit
from app.services.fhir_client import FHIRClient
from app.services.fhir_mapping import (
    encounter_to_fhir,
    observation_to_fhir,
    patient_to_fhir,
)


def sync_patient(
    db: Session,
    *,
    patient: Patient,
    actor_id: uuid.UUID,
    client: FHIRClient | None = None,
) -> dict:
    client = client or FHIRClient()
    payload = patient_to_fhir(patient)
    result = client.upsert("Patient", payload, patient.fhir_patient_id)
    fhir_id = result.resource.get("id")
    if not fhir_id:
        raise RuntimeError("HAPI FHIR no devolvió id para Patient")

    patient.fhir_patient_id = fhir_id
    add_audit(
        db,
        user_id=actor_id,
        action="FHIR_CREATE" if result.created else "FHIR_UPDATE",
        entity_type="PATIENT",
        entity_id=patient.id,
        details={"fhir_id": fhir_id},
    )
    db.commit()
    db.refresh(patient)
    return result.resource


def sync_encounter(
    db: Session,
    *,
    encounter: Encounter,
    actor_id: uuid.UUID,
    client: FHIRClient | None = None,
) -> dict:
    client = client or FHIRClient()
    patient = db.get(Patient, encounter.patient_id)
    if patient is None:
        raise RuntimeError("Paciente local no encontrado")

    if not patient.fhir_patient_id:
        sync_patient(db, patient=patient, actor_id=actor_id, client=client)

    payload = encounter_to_fhir(
        encounter,
        patient_fhir_id=patient.fhir_patient_id,
    )
    result = client.upsert(
        "Encounter",
        payload,
        encounter.fhir_encounter_id,
    )
    fhir_id = result.resource.get("id")
    if not fhir_id:
        raise RuntimeError("HAPI FHIR no devolvió id para Encounter")

    encounter.fhir_encounter_id = fhir_id
    add_audit(
        db,
        user_id=actor_id,
        action="FHIR_CREATE" if result.created else "FHIR_UPDATE",
        entity_type="ENCOUNTER",
        entity_id=encounter.id,
        details={"fhir_id": fhir_id},
    )
    db.commit()
    db.refresh(encounter)
    return result.resource


def sync_observation(
    db: Session,
    *,
    observation: Observation,
    actor_id: uuid.UUID,
    client: FHIRClient | None = None,
) -> dict:
    client = client or FHIRClient()
    patient = db.get(Patient, observation.patient_id)
    if patient is None:
        raise RuntimeError("Paciente local no encontrado")

    if not patient.fhir_patient_id:
        sync_patient(db, patient=patient, actor_id=actor_id, client=client)

    encounter_fhir_id = None
    if observation.encounter_id:
        encounter = db.get(Encounter, observation.encounter_id)
        if encounter:
            if not encounter.fhir_encounter_id:
                sync_encounter(
                    db,
                    encounter=encounter,
                    actor_id=actor_id,
                    client=client,
                )
            encounter_fhir_id = encounter.fhir_encounter_id

    payload = observation_to_fhir(
        observation,
        patient_fhir_id=patient.fhir_patient_id,
        encounter_fhir_id=encounter_fhir_id,
    )
    result = client.upsert(
        "Observation",
        payload,
        observation.fhir_observation_id,
    )
    fhir_id = result.resource.get("id")
    if not fhir_id:
        raise RuntimeError("HAPI FHIR no devolvió id para Observation")

    observation.fhir_observation_id = fhir_id
    add_audit(
        db,
        user_id=actor_id,
        action="FHIR_CREATE" if result.created else "FHIR_UPDATE",
        entity_type="OBSERVATION",
        entity_id=observation.id,
        details={"fhir_id": fhir_id},
    )
    db.commit()
    db.refresh(observation)
    return result.resource


def sync_patient_all(
    db: Session,
    *,
    patient: Patient,
    actor_id: uuid.UUID,
) -> dict:
    client = FHIRClient()
    patient_resource = sync_patient(
        db,
        patient=patient,
        actor_id=actor_id,
        client=client,
    )

    encounters = list(
        db.scalars(
            select(Encounter).where(
                Encounter.patient_id == patient.id,
                Encounter.is_active.is_(True),
                Encounter.deleted_at.is_(None),
            )
        )
    )
    encounter_resources = [
        sync_encounter(
            db,
            encounter=encounter,
            actor_id=actor_id,
            client=client,
        )
        for encounter in encounters
    ]

    observations = list(
        db.scalars(
            select(Observation).where(
                Observation.patient_id == patient.id,
                Observation.is_active.is_(True),
                Observation.deleted_at.is_(None),
            )
        )
    )
    observation_resources = [
        sync_observation(
            db,
            observation=observation,
            actor_id=actor_id,
            client=client,
        )
        for observation in observations
    ]

    return {
        "patient": patient_resource,
        "encounters": encounter_resources,
        "observations": observation_resources,
        "summary": {
            "Patient": 1,
            "Encounter": len(encounter_resources),
            "Observation": len(observation_resources),
        },
    }
