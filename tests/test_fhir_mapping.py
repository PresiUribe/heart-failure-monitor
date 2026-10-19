from datetime import date, datetime
from decimal import Decimal

from app.models.encounter import Encounter
from app.models.observation import Observation
from app.models.observation_component import ObservationComponent
from app.models.patient import Patient
from app.services.fhir_mapping import (
    encounter_to_fhir,
    observation_to_fhir,
    patient_to_fhir,
)


def test_patient_mapping():
    patient = Patient(
        document_number="1001",
        first_name="Juan",
        last_name="Perez",
        birth_date=date(1962, 4, 15),
        sex="MALE",
        municipality="Cali",
        rural_area="Zona rural",
    )
    resource = patient_to_fhir(patient)
    assert resource["resourceType"] == "Patient"
    assert resource["gender"] == "male"
    assert resource["identifier"][0]["value"] == "1001"


def test_encounter_mapping():
    encounter = Encounter(
        patient_id="00000000-0000-0000-0000-000000000001",
        created_by="00000000-0000-0000-0000-000000000002",
        encounter_type="CONSULTA_RURAL",
        status="FINISHED",
        start_at=datetime(2026, 9, 6, 10, 0, 0),
    )
    resource = encounter_to_fhir(encounter, patient_fhir_id="123")
    assert resource["resourceType"] == "Encounter"
    assert resource["status"] == "finished"
    assert resource["subject"]["reference"] == "Patient/123"


def test_blood_pressure_mapping():
    observation = Observation(
        patient_id="00000000-0000-0000-0000-000000000001",
        created_by="00000000-0000-0000-0000-000000000002",
        code_system="http://loinc.org",
        code="85354-9",
        display="Blood pressure panel",
        value_type="COMPONENTS",
        measured_at=datetime(2026, 9, 6, 10, 0, 0),
    )
    observation.components = [
        ObservationComponent(
            code_system="http://loinc.org",
            code="8480-6",
            display="Systolic blood pressure",
            value_numeric=Decimal("142"),
            unit="mmHg",
        ),
        ObservationComponent(
            code_system="http://loinc.org",
            code="8462-4",
            display="Diastolic blood pressure",
            value_numeric=Decimal("90"),
            unit="mmHg",
        ),
    ]

    resource = observation_to_fhir(
        observation,
        patient_fhir_id="123",
    )
    assert resource["resourceType"] == "Observation"
    assert len(resource["component"]) == 2
    assert resource["component"][0]["valueQuantity"]["value"] == 142.0
