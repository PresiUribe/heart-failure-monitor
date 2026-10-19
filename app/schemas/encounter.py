import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EncounterType = Literal[
    "CONSULTA_RURAL",
    "CONTROL",
    "TELECONSULTA",
    "REMISION",
    "URGENCIA",
]

EncounterStatus = Literal[
    "PLANNED",
    "IN_PROGRESS",
    "FINISHED",
    "CANCELLED",
]


class EncounterCreate(BaseModel):
    encounter_type: EncounterType
    reason: str | None = Field(default=None, max_length=255)
    clinical_notes: str | None = None
    status: EncounterStatus = "IN_PROGRESS"
    start_at: datetime | None = None
    end_at: datetime | None = None


class EncounterUpdate(BaseModel):
    encounter_type: EncounterType | None = None
    reason: str | None = Field(default=None, max_length=255)
    clinical_notes: str | None = None
    status: EncounterStatus | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class EncounterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    created_by: uuid.UUID
    encounter_type: str
    reason: str | None
    clinical_notes: str | None
    status: str
    start_at: datetime
    end_at: datetime | None
    fhir_encounter_id: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
