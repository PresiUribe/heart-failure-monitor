import asyncio
import logging
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies.auth import require_roles
from app.models.patient import Patient
from app.models.patient_assignment import PatientAssignment
from app.models.referral import Referral
from app.models.user import User
from app.schemas.corte2 import ReferralCreate
from app.services.access import ensure_patient_access
from app.services.identifiers import resolve_patient_identifier, resolve_user_identifier
from app.services.audit import add_audit
from app.services.notifications import create_notification, publish_notification
from app.services.roles import canonical_role
from app.services.fhir_client import FHIRClient
from app.services.fhir_sync import sync_patient
from app.database import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Remisiones"])

@router.get("/directory/specialists")
def specialist_directory(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("MEDICO", "ESPECIALISTA", "ADMIN")),
):
    users = list(db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))))
    return [
        {"id": str(u.id), "document_number": u.document_number, "user_code": u.user_code, "full_name": u.full_name or u.username}
        for u in users
        if canonical_role(u.role.code) == "especialista"
    ]


@router.post("/patients/{patient_id}/referrals", status_code=201)
async def create_referral(patient_id: str, payload: ReferralCreate, db: Session = Depends(get_db), current_user: User = Depends(require_roles("MEDICO","ADMIN"))):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db, user=current_user, patient_id=patient.id)
    if payload.specialist_id:
        specialist = resolve_user_identifier(db, payload.specialist_id, include_inactive=False)
    else:
        specialist = next(
            (u for u in db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None)).order_by(User.created_at))
             if canonical_role(u.role.code) == "especialista"),
            None,
        )
        if specialist is None:
            raise HTTPException(status_code=400, detail="No hay especialistas activos para recibir la remisión")
    if canonical_role(specialist.role.code) != "especialista":
        raise HTTPException(status_code=400, detail="El destinatario debe ser especialista")
    ref = Referral(patient_id=patient.id, referred_by=current_user.id, specialist_id=specialist.id, reason=payload.reason)
    db.add(ref); db.flush()
    n = create_notification(db, user_id=specialist.id, tipo="remision_recibida", mensaje=f"Nueva remisión del paciente {patient.patient_code}: {payload.reason}", patient_id=patient.id, data={"remision_id":str(ref.id)})
    add_audit(db, user_id=current_user.id, action="REFERRAL_CREATED", entity_type="REFERRAL", entity_id=ref.id, details={"patient_id":str(patient.id),"specialist_id":str(specialist.id)})
    db.commit(); db.refresh(ref); db.refresh(n)
    await publish_notification(n)
    # Reflejo FHIR (ServiceRequest) en segundo plano: no bloquea ni hace fallar la remisión.
    asyncio.create_task(asyncio.to_thread(_sync_service_request, ref.id, current_user.id))
    return {"id":str(ref.id),"paciente_id":str(patient.id),"especialista_id":str(specialist.id),"estado":"pendiente"}


def _sync_service_request(referral_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    """Publica la remisión como FHIR R4 ServiceRequest (SNOMED 3457005 = referral)."""
    try:
        with SessionLocal() as db:
            ref = db.get(Referral, referral_id)
            patient = db.get(Patient, ref.patient_id)
            client = FHIRClient()
            if not patient.fhir_patient_id:
                sync_patient(db, patient=patient, actor_id=actor_id, client=client)
            resource = {
                "resourceType": "ServiceRequest",
                "identifier": [{"system": "urn:salud-rural:referral-id", "value": str(ref.id)}],
                "status": "active",
                "intent": "order",
                "category": [{"coding": [{"system": "http://snomed.info/sct", "code": "3457005", "display": "Patient referral"}]}],
                "code": {"coding": [{"system": "http://snomed.info/sct", "code": "183519002", "display": "Referral to cardiology service"}]},
                "subject": {"reference": f"Patient/{patient.fhir_patient_id}"},
                "authoredOn": ref.created_at.replace(microsecond=0).isoformat() if ref.created_at else None,
                "reasonCode": [{"text": ref.reason}],
            }
            result = client.upsert("ServiceRequest", {k: v for k, v in resource.items() if v is not None})
            add_audit(db, user_id=actor_id, action="FHIR_CREATE", entity_type="REFERRAL", entity_id=ref.id,
                      details={"resource": "ServiceRequest", "fhir_id": result.resource.get("id"), "patient_id": str(patient.id)})
            db.commit()
    except Exception as exc:  # FHIR es un reflejo; la remisión local ya quedó registrada.
        logger.warning("No se pudo reflejar la remisión %s como ServiceRequest: %s", referral_id, exc)

@router.get("/referrals")
def list_referrals(db: Session = Depends(get_db), current_user: User = Depends(require_roles("MEDICO","ESPECIALISTA","ADMIN"))):
    role=canonical_role(current_user.role.code)
    q=select(Referral)
    if role=="especialista": q=q.where(Referral.specialist_id==current_user.id)
    elif role=="medico": q=q.where(Referral.referred_by==current_user.id)
    rows=list(db.scalars(q.order_by(Referral.created_at.desc())))
    return [{"id":str(r.id),"paciente_id":str(r.patient_id),"medico_id":str(r.referred_by),"especialista_id":str(r.specialist_id),"motivo":r.reason,"estado":{"PENDING":"pendiente","ACCEPTED":"aceptada","REJECTED":"rechazada"}.get(r.status, r.status.lower()),"fecha":r.created_at} for r in rows]

@router.post("/referrals/{referral_id}/accept")
async def accept_referral(referral_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(require_roles("ESPECIALISTA","ADMIN"))):
    ref=db.get(Referral, referral_id)
    if ref is None: raise HTTPException(status_code=404, detail="Remisión no encontrada")
    if canonical_role(current_user.role.code)!="admin" and ref.specialist_id!=current_user.id: raise HTTPException(status_code=403, detail="Esta remisión pertenece a otro especialista")
    if ref.status!="PENDING": raise HTTPException(status_code=409, detail="La remisión ya fue resuelta")
    ref.status="ACCEPTED"; ref.accepted_at=datetime.utcnow()
    existing_assignment = db.scalar(
        select(PatientAssignment).where(
            PatientAssignment.patient_id == ref.patient_id,
            PatientAssignment.user_id == ref.specialist_id,
            PatientAssignment.relationship_type == "ESPECIALISTA",
        )
    )
    if existing_assignment is None:
        db.add(PatientAssignment(
            patient_id=ref.patient_id,
            user_id=ref.specialist_id,
            relationship_type="ESPECIALISTA",
            is_primary=False,
            active=True,
        ))
    elif not existing_assignment.active:
        existing_assignment.active = True
    n=create_notification(db,user_id=ref.referred_by,tipo="remision_aceptada",mensaje="El especialista aceptó la remisión.",patient_id=ref.patient_id,data={"remision_id":str(ref.id)})
    add_audit(db,user_id=current_user.id,action="REFERRAL_ACCEPTED",entity_type="REFERRAL",entity_id=ref.id,details={"patient_id":str(ref.patient_id)})
    db.commit(); db.refresh(n)
    await publish_notification(n)
    return {"id":str(ref.id),"estado":"aceptada"}
