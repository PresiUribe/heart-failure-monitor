import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ValueType = Literal["NUMERIC", "TEXT", "BOOLEAN", "COMPONENTS"]


class ObservationComponentCreate(BaseModel):
    code_system: str = "http://loinc.org"
    code: str = Field(min_length=1, max_length=100)
    display: str | None = Field(default=None, max_length=255)
    value_numeric: Decimal
    unit: str | None = Field(default=None, max_length=50)


class ObservationComponentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    observation_id: uuid.UUID
    code_system: str | None
    code: str
    display: str | None
    value_numeric: Decimal
    unit: str | None


class ObservationCreate(BaseModel):
    encounter_id: uuid.UUID | None = None
    code_system: str = "http://loinc.org"
    code: str = Field(min_length=1, max_length=100)
    display: str | None = Field(default=None, max_length=255)
    value_type: ValueType
    value_numeric: Decimal | None = None
    value_text: str | None = None
    value_boolean: bool | None = None
    unit: str | None = Field(default=None, max_length=50)
    measured_at: datetime | None = None
    components: list[ObservationComponentCreate] = []

    @model_validator(mode="after")
    def validate_value(self):
        if self.value_type == "NUMERIC" and self.value_numeric is None:
            raise ValueError("value_numeric es obligatorio cuando value_type=NUMERIC")
        if self.value_type == "TEXT" and self.value_text is None:
            raise ValueError("value_text es obligatorio cuando value_type=TEXT")
        if self.value_type == "BOOLEAN" and self.value_boolean is None:
            raise ValueError("value_boolean es obligatorio cuando value_type=BOOLEAN")
        if self.value_type == "COMPONENTS" and not self.components:
            raise ValueError("components es obligatorio cuando value_type=COMPONENTS")
        return self


class ObservationUpdate(BaseModel):
    encounter_id: uuid.UUID | None = None
    display: str | None = Field(default=None, max_length=255)
    value_numeric: Decimal | None = None
    value_text: str | None = None
    value_boolean: bool | None = None
    unit: str | None = Field(default=None, max_length=50)
    measured_at: datetime | None = None


class ObservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    encounter_id: uuid.UUID | None
    created_by: uuid.UUID
    code_system: str | None
    code: str | None
    display: str | None
    value_type: str
    value_numeric: Decimal | None
    value_text: str | None
    value_boolean: bool | None
    unit: str | None
    measured_at: datetime
    fhir_observation_id: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    components: list[ObservationComponentResponse] = []


class BloodPressureCreate(BaseModel):
    encounter_id: uuid.UUID | None = None
    systolic: Decimal
    diastolic: Decimal
    measured_at: datetime | None = None
