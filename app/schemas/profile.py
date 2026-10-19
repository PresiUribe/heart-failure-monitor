from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class DoctorProfileInfo(BaseModel):
    user_code: str
    name: str
    role: str
    encounter_type: str
    attended_at: datetime


class WeightProfileInfo(BaseModel):
    value: Decimal
    unit: str | None
    measured_at: datetime


class BloodPressureProfileInfo(BaseModel):
    systolic: Decimal | None
    diastolic: Decimal | None
    unit: str = "mmHg"
    measured_at: datetime


class PatientProfileResponse(BaseModel):
    patient_code: str
    full_name: str
    doctor: DoctorProfileInfo | None
    weight: WeightProfileInfo | None
    blood_pressure: BloodPressureProfileInfo | None
