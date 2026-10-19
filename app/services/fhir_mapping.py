from datetime import datetime, timezone

from app.models.encounter import Encounter
from app.models.observation import Observation
from app.models.patient import Patient


def _fhir_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def patient_to_fhir(patient: Patient) -> dict:
    resource = {
        "resourceType": "Patient",
        "identifier": [
            {
                "system": "urn:salud-rural:document-number",
                "value": patient.document_number,
            },
            {
                "system": "urn:salud-rural:patient-id",
                "value": str(patient.id),
            },
            {
                "system": "urn:salud-rural:patient-code",
                "value": patient.patient_code,
            },
        ],
        "name": [
            {
                "use": "official",
                "family": patient.last_name,
                "given": [patient.first_name],
            }
        ],
        "active": patient.is_active,
    }

    if patient.fhir_patient_id:
        resource["id"] = patient.fhir_patient_id

    if patient.birth_date:
        resource["birthDate"] = patient.birth_date.isoformat()

    if patient.sex:
        resource["gender"] = patient.sex.lower()

    if patient.municipality or patient.rural_area:
        address = {}
        if patient.municipality:
            address["city"] = patient.municipality
        if patient.rural_area:
            address["text"] = patient.rural_area
        resource["address"] = [address]

    return resource


def encounter_to_fhir(encounter: Encounter, *, patient_fhir_id: str) -> dict:
    status_map = {
        "PLANNED": "planned",
        "IN_PROGRESS": "in-progress",
        "FINISHED": "finished",
        "CANCELLED": "cancelled",
    }

    resource = {
        "resourceType": "Encounter",
        "status": status_map[encounter.status],
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
            "display": "ambulatory",
        },
        "type": [
            {
                "text": encounter.encounter_type.replace("_", " ").title()
            }
        ],
        "subject": {
            "reference": f"Patient/{patient_fhir_id}"
        },
        "period": {
            "start": _fhir_datetime(encounter.start_at)
        },
    }

    if encounter.fhir_encounter_id:
        resource["id"] = encounter.fhir_encounter_id

    if encounter.end_at:
        resource["period"]["end"] = _fhir_datetime(encounter.end_at)

    if encounter.reason:
        resource["reasonCode"] = [{"text": encounter.reason}]

    # `Encounter.note` no existe en HL7 FHIR R4. La nota clínica se conserva
    # en PostgreSQL, pero no se fuerza dentro del recurso Encounter obligatorio.

    return resource


def observation_to_fhir(
    observation: Observation,
    *,
    patient_fhir_id: str,
    encounter_fhir_id: str | None = None,
) -> dict:
    coding = {}
    if observation.code_system:
        coding["system"] = observation.code_system
    if observation.code:
        coding["code"] = observation.code
    if observation.display:
        coding["display"] = observation.display

    resource = {
        "resourceType": "Observation",
        "status": "final",
        "code": {
            "coding": [coding] if coding else [],
            "text": observation.display or observation.code or "Observation",
        },
        "subject": {
            "reference": f"Patient/{patient_fhir_id}"
        },
        "effectiveDateTime": _fhir_datetime(observation.measured_at),
    }

    if observation.fhir_observation_id:
        resource["id"] = observation.fhir_observation_id

    if encounter_fhir_id:
        resource["encounter"] = {
            "reference": f"Encounter/{encounter_fhir_id}"
        }

    vital_codes = {"29463-7", "8867-4", "85354-9", "8480-6", "8462-4"}
    if observation.code_system == "http://loinc.org" and observation.code in vital_codes:
        resource["category"] = [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "vital-signs",
                        "display": "Vital Signs",
                    }
                ]
            }
        ]

    if observation.value_type == "NUMERIC":
        quantity = {
            "value": float(observation.value_numeric),
        }
        if observation.unit:
            quantity["unit"] = observation.unit
        resource["valueQuantity"] = quantity
    elif observation.value_type == "TEXT":
        resource["valueString"] = observation.value_text
    elif observation.value_type == "BOOLEAN":
        resource["valueBoolean"] = observation.value_boolean
    elif observation.value_type == "COMPONENTS":
        resource["component"] = []
        for component in observation.components:
            component_coding = {
                "code": component.code,
            }
            if component.code_system:
                component_coding["system"] = component.code_system
            if component.display:
                component_coding["display"] = component.display

            resource["component"].append(
                {
                    "code": {
                        "coding": [component_coding],
                        "text": component.display or component.code,
                    },
                    "valueQuantity": {
                        **{"value": float(component.value_numeric)},
                        **({"unit": component.unit} if component.unit else {}),
                    },
                }
            )

    return resource
