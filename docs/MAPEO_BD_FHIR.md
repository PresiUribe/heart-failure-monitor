# Mapeo de la base de datos a HL7 FHIR R4

## Patient

Origen: `patients`

| PostgreSQL | FHIR R4 |
|---|---|
| document_number | Patient.identifier |
| first_name | Patient.name.given |
| last_name | Patient.name.family |
| birth_date | Patient.birthDate |
| sex | Patient.gender |
| municipality / rural_area | Patient.address |
| is_active | Patient.active |
| fhir_patient_id | ID asignado por HAPI |

`primary_diagnosis` se conserva en el modelo relacional. No se fuerza dentro de
Patient porque el diagnóstico clínico tiene un modelado FHIR propio; para esta
entrega el alcance obligatorio se concentra en Patient, Encounter y Observation.

## Encounter

Origen: `encounters`

| PostgreSQL | FHIR R4 |
|---|---|
| encounter_type | Encounter.type |
| status | Encounter.status |
| start_at / end_at | Encounter.period |
| reason | Encounter.reasonCode.text |
| clinical_notes | Encounter.note |
| patient_id + fhir_patient_id | Encounter.subject |
| fhir_encounter_id | ID asignado por HAPI |

Los estados locales se transforman:
- PLANNED -> planned
- IN_PROGRESS -> in-progress
- FINISHED -> finished
- CANCELLED -> cancelled

## Observation

Origen: `observations`

| PostgreSQL | FHIR R4 |
|---|---|
| code_system | Observation.code.coding.system |
| code | Observation.code.coding.code |
| display | Observation.code.coding.display |
| measured_at | Observation.effectiveDateTime |
| value_numeric + unit | Observation.valueQuantity |
| value_text | Observation.valueString |
| value_boolean | Observation.valueBoolean |
| patient_id | Observation.subject |
| encounter_id | Observation.encounter |
| fhir_observation_id | ID asignado por HAPI |

## Presión arterial

`observations` almacena el panel LOINC `85354-9`.

`observation_components` almacena:
- sistólica: LOINC `8480-6`
- diastólica: LOINC `8462-4`

En FHIR se transforman en `Observation.component`.

## Otros códigos utilizados en la demostración

- Peso: LOINC `29463-7`
- Frecuencia cardiaca: LOINC `8867-4`
- Presión arterial: LOINC `85354-9`
- Sistólica: LOINC `8480-6`
- Diastólica: LOINC `8462-4`

Para síntomas puede utilizarse SNOMED CT según el síntoma que se quiera representar.
