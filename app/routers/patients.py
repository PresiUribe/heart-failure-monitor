import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import require_roles
from app.models.patient import Patient
from app.models.patient_assignment import PatientAssignment
from app.models.user import User
from app.schemas.assignment import AssignmentCreate, AssignmentResponse
from app.schemas.patient import PatientCreate, PatientResponse, PatientUpdate
from app.services.access import ensure_patient_access
from app.services.audit import add_audit, add_history, model_snapshot
from app.services.identifiers import resolve_patient_identifier, resolve_user_identifier

from app.services.fhir_sync import sync_patient
from app.services.roles import canonical_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/patients", tags=["Pacientes"])


def get_patient_any_state(db: Session, patient_identifier: str) -> Patient:
    return resolve_patient_identifier(db, patient_identifier, include_inactive=True)


def get_active_patient(db: Session, patient_identifier: str) -> Patient:
    return resolve_patient_identifier(db, patient_identifier, include_inactive=False)


@router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def create_patient(
    payload: PatientCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO")),
):
    existing = db.scalar(select(Patient).where(Patient.document_number == payload.document_number))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Ya existe un paciente con ese documento")

    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.flush()

    if current_user.role.code in {"MEDICO", "MEDICO_RURAL"}:
        db.add(
            PatientAssignment(
                patient_id=patient.id,
                user_id=current_user.id,
                relationship_type="MEDICO_RURAL",
                is_primary=True,
            )
        )

    add_audit(
        db,
        user_id=current_user.id,
        action="CREATE",
        entity_type="PATIENT",
        entity_id=patient.id,
        details={"patient_code": patient.patient_code, "document_number": patient.document_number},
    )
    db.commit()
    db.refresh(patient)
    # R15: el paciente creado queda disponible en HAPI FHIR por su identificador.
    # Best effort: si HAPI no responde, el alta local no se pierde y se puede
    # sincronizar luego con POST /fhir/patients/{id}/sync.
    try:
        sync_patient(db, patient=patient, actor_id=current_user.id)
    except Exception as exc:
        db.rollback()
        logger.warning("Sincronización FHIR automática falló para %s: %s", patient.id, exc)
    db.refresh(patient)
    return patient


@router.get("", response_model=list[PatientResponse])
def list_patients(
    include_deleted: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO", "ESPECIALISTA", "PACIENTE")),
):
    if include_deleted and current_user.role.code != "ADMIN":
        raise HTTPException(status_code=403, detail="Solo ADMIN puede consultar pacientes eliminados")

    query = select(Patient)
    if not include_deleted:
        query = query.where(Patient.is_active.is_(True), Patient.deleted_at.is_(None))

    if current_user.role.code != "ADMIN":
        query = (
            query.join(PatientAssignment, PatientAssignment.patient_id == Patient.id)
            .where(
                PatientAssignment.user_id == current_user.id,
                PatientAssignment.active.is_(True),
            )
            .distinct()
        )

    return list(db.scalars(query.order_by(Patient.patient_code.asc())))


@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO", "ESPECIALISTA", "PACIENTE")),
):
    patient = get_active_patient(db, patient_id)
    ensure_patient_access(db, user=current_user, patient_id=patient.id)
    return patient


@router.put("/{patient_id}", response_model=PatientResponse)
def update_patient(
    patient_id: str,
    payload: PatientUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN")),
):
    patient = get_active_patient(db, patient_id)
    previous = model_snapshot(patient)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(patient, field, value)
    patient.updated_at = datetime.utcnow()
    add_history(
        db,
        entity_type="PATIENT",
        entity_id=patient.id,
        previous_data=previous,
        new_data=model_snapshot(patient),
        changed_by=current_user.id,
        change_type="SOFT_EDIT",
    )
    add_audit(db, user_id=current_user.id, action="SOFT_EDIT", entity_type="PATIENT", entity_id=patient.id, details={"fields": list(updates.keys())})
    db.commit()
    db.refresh(patient)
    return patient


@router.delete("/{patient_id}", response_model=PatientResponse)
def soft_delete_patient(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO")),
):
    patient = get_active_patient(db, patient_id)
    # R14: el médico solo puede enviar a la papelera los pacientes que él registró
    # (asignación primaria MEDICO_RURAL). Solo ADMIN restaura.
    if canonical_role(current_user.role.code) != "admin":
        owner = db.scalar(
            select(PatientAssignment.id).where(
                PatientAssignment.patient_id == patient.id,
                PatientAssignment.user_id == current_user.id,
                PatientAssignment.relationship_type == "MEDICO_RURAL",
                PatientAssignment.is_primary.is_(True),
            )
        )
        if owner is None:
            raise HTTPException(status_code=403, detail="Solo puede eliminar los pacientes que usted registró")
    previous = model_snapshot(patient)
    patient.is_active = False
    patient.deleted_at = datetime.utcnow()
    patient.deleted_by = current_user.id
    patient.updated_at = datetime.utcnow()
    add_history(db, entity_type="PATIENT", entity_id=patient.id, previous_data=previous, new_data=model_snapshot(patient), changed_by=current_user.id, change_type="SOFT_DELETE")
    add_audit(db, user_id=current_user.id, action="SOFT_DELETE", entity_type="PATIENT", entity_id=patient.id)
    db.commit()
    db.refresh(patient)
    return patient


@router.post("/{patient_id}/restore", response_model=PatientResponse)
def restore_patient(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN")),
):
    patient = get_patient_any_state(db, patient_id)
    if patient.is_active and patient.deleted_at is None:
        raise HTTPException(status_code=409, detail="El paciente ya está activo")
    previous = model_snapshot(patient)
    patient.is_active = True
    patient.deleted_at = None
    patient.deleted_by = None
    patient.updated_at = datetime.utcnow()
    add_history(db, entity_type="PATIENT", entity_id=patient.id, previous_data=previous, new_data=model_snapshot(patient), changed_by=current_user.id, change_type="RESTORE")
    add_audit(db, user_id=current_user.id, action="RESTORE", entity_type="PATIENT", entity_id=patient.id)
    db.commit()
    db.refresh(patient)
    return patient


@router.post("/{patient_id}/assignments", response_model=AssignmentResponse, status_code=status.HTTP_201_CREATED)
def assign_user_to_patient(
    patient_id: str,
    payload: AssignmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN")),
):
    patient = get_active_patient(db, patient_id)
    user = resolve_user_identifier(db, payload.user_id, include_inactive=False)

    expected_role = {
        "PACIENTE": "PACIENTE",
        "CUIDADOR": "PACIENTE",
        "MEDICO_RURAL": "MEDICO",
        "ESPECIALISTA": "ESPECIALISTA",
    }[payload.relationship_type]
    from app.services.roles import canonical_role
    if canonical_role(user.role.code) != canonical_role(expected_role):
        raise HTTPException(status_code=400, detail=f"El usuario con rol {user.role.code} no puede tener la relación {payload.relationship_type}")

    assignment = PatientAssignment(
        patient_id=patient.id,
        user_id=user.id,
        relationship_type=payload.relationship_type,
        is_primary=payload.is_primary,
    )
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="El usuario ya tiene esa relación con el paciente")

    add_audit(
        db,
        user_id=current_user.id,
        action="ASSIGN_USER",
        entity_type="PATIENT_ASSIGNMENT",
        entity_id=assignment.id,
        details={
            "patient_code": patient.patient_code,
            "assigned_user_code": user.user_code,
            "relationship_type": payload.relationship_type,
        },
    )
    db.commit()
    db.refresh(assignment)
    return assignment


@router.get("/{patient_id}/assignments", response_model=list[AssignmentResponse])
def list_patient_assignments(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "MEDICO", "ESPECIALISTA", "PACIENTE")),
):
    patient = get_active_patient(db, patient_id)
    ensure_patient_access(db, user=current_user, patient_id=patient.id)
    return list(
        db.scalars(
            select(PatientAssignment)
            .where(PatientAssignment.patient_id == patient.id, PatientAssignment.active.is_(True))
            .order_by(PatientAssignment.assigned_at.asc())
        )
    )
