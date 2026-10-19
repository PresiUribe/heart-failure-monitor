from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import require_roles
from app.models.encounter import Encounter
from app.models.observation import Observation
from app.models.patient import Patient
from app.models.patient_assignment import PatientAssignment
from app.models.role import Role
from app.models.user import User
from app.schemas.profile import (
    BloodPressureProfileInfo,
    DoctorProfileInfo,
    PatientProfileResponse,
    WeightProfileInfo,
)

router = APIRouter(prefix="/patient-profile", tags=["Perfil del paciente"])


@router.get("/me", response_model=PatientProfileResponse)
def my_patient_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("PACIENTE")),
):
    assignment = db.scalar(
        select(PatientAssignment)
        .where(
            PatientAssignment.user_id == current_user.id,
            PatientAssignment.relationship_type == "PACIENTE",
            PatientAssignment.active.is_(True),
        )
        .order_by(PatientAssignment.is_primary.desc(), PatientAssignment.assigned_at.desc())
    )
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Este usuario no está vinculado a un perfil de paciente",
        )

    patient = db.scalar(
        select(Patient).where(
            Patient.id == assignment.patient_id,
            Patient.is_active.is_(True),
            Patient.deleted_at.is_(None),
        )
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="Paciente no encontrado o inactivo")

    doctor_row = db.execute(
        select(Encounter, User, Role)
        .join(User, User.id == Encounter.created_by)
        .join(Role, Role.id == User.role_id)
        .where(
            Encounter.patient_id == patient.id,
            Encounter.is_active.is_(True),
            Encounter.deleted_at.is_(None),
            Role.code.in_(["MEDICO", "MEDICO_RURAL", "ESPECIALISTA"]),
        )
        .order_by(Encounter.start_at.desc())
        .limit(1)
    ).first()

    doctor = None
    if doctor_row:
        encounter, doctor_user, doctor_role = doctor_row
        doctor = DoctorProfileInfo(
            user_code=doctor_user.user_code,
            name=doctor_user.full_name or doctor_user.username,
            role=doctor_role.code,
            encounter_type=encounter.encounter_type,
            attended_at=encounter.start_at,
        )

    weight_obs = db.scalar(
        select(Observation)
        .where(
            Observation.patient_id == patient.id,
            Observation.code == "29463-7",
            Observation.value_type == "NUMERIC",
            Observation.is_active.is_(True),
            Observation.deleted_at.is_(None),
        )
        .order_by(Observation.measured_at.desc())
        .limit(1)
    )
    weight = None
    if weight_obs and weight_obs.value_numeric is not None:
        weight = WeightProfileInfo(
            value=weight_obs.value_numeric,
            unit=weight_obs.unit,
            measured_at=weight_obs.measured_at,
        )

    bp_obs = db.scalar(
        select(Observation)
        .where(
            Observation.patient_id == patient.id,
            Observation.code == "85354-9",
            Observation.value_type == "COMPONENTS",
            Observation.is_active.is_(True),
            Observation.deleted_at.is_(None),
        )
        .order_by(Observation.measured_at.desc())
        .limit(1)
    )
    blood_pressure = None
    if bp_obs:
        systolic = next((c.value_numeric for c in bp_obs.components if c.code == "8480-6"), None)
        diastolic = next((c.value_numeric for c in bp_obs.components if c.code == "8462-4"), None)
        blood_pressure = BloodPressureProfileInfo(
            systolic=systolic,
            diastolic=diastolic,
            measured_at=bp_obs.measured_at,
        )

    return PatientProfileResponse(
        patient_code=patient.patient_code,
        full_name=f"{patient.first_name} {patient.last_name}",
        doctor=doctor,
        weight=weight,
        blood_pressure=blood_pressure,
    )
