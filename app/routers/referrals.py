import asyncio
import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies.auth import require_roles
from app.models.patient import Patient
from app.models.patient_assignment import PatientAssignment
from app.models.referral import Referral
from app.models.interop import RemisionExterna, Socio
from app.interop.servicio import responder_socio
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
    return await registrar_remision(db, patient=patient, current_user=current_user, reason=payload.reason,
                                    specialist_id=payload.specialist_id)


async def registrar_remision(db: Session, *, patient: Patient, current_user: User, reason: str,
                             specialist_id: str | None = None, origen: str = "api") -> dict:
    """Crea la remisión, notifica al especialista, audita y la refleja en FHIR.

    La usan el endpoint del Corte 2 y la herramienta proponer_remision del agente (Semana 13),
    así el agente no tiene un camino paralelo: dispara las mismas notificaciones y auditoría.
    """
    if specialist_id:
        specialist = resolve_user_identifier(db, specialist_id, include_inactive=False)
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
    ref = Referral(patient_id=patient.id, referred_by=current_user.id, specialist_id=specialist.id, reason=reason)
    db.add(ref); db.flush()
    n = create_notification(db, user_id=specialist.id, tipo="remision_recibida", mensaje=f"Nueva remisión del paciente {patient.patient_code}: {reason}", patient_id=patient.id, data={"remision_id":str(ref.id)})
    add_audit(db, user_id=current_user.id, action="REFERRAL_CREATED", entity_type="REFERRAL", entity_id=ref.id, details={"patient_id":str(patient.id),"specialist_id":str(specialist.id),"origen":origen})
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
    q=select(Referral, RemisionExterna, Socio).outerjoin(RemisionExterna, RemisionExterna.referral_id==Referral.id).outerjoin(Socio, Socio.id==RemisionExterna.socio_id)
    if role=="especialista": q=q.where(Referral.specialist_id==current_user.id)
    elif role=="medico": q=q.where(Referral.referred_by==current_user.id)
    rows=db.execute(q.order_by(Referral.created_at.desc())).all()
    salida=[]
    for r, rx, socio in rows:
        item={"id":str(r.id),"paciente_id":str(r.patient_id),"medico_id":str(r.referred_by),"especialista_id":str(r.specialist_id),"motivo":r.reason,"estado":{"PENDING":"pendiente","ACCEPTED":"aceptada","REJECTED":"rechazada"}.get(r.status, r.status.lower()),"fecha":r.created_at,"respuesta":r.response_note,"origen":"interno"}
        if rx is not None:
            item.update({"origen":"externo","institucion":socio.codigo,"institucion_nombre":socio.nombre,"remision_externa":f"rem-{rx.numero}","remision_externa_id":str(rx.id),
                         "prioridad":rx.prioridad,"especialidad":rx.especialidad,"observaciones":rx.observaciones,"reporte_ia":(rx.reporte or {}).get("conclusion"),
                         "respuesta_envio":rx.respuesta_envio})
        salida.append(item)
    return salida


class ReferralAccept(BaseModel):
    nota: str | None = Field(default=None, max_length=1000)  # opcional: viaja al socio como statusReason


class ReferralReject(BaseModel):
    motivo: str | None = Field(default=None, min_length=5, max_length=1000)
    reason: str | None = Field(default=None, min_length=5, max_length=1000)


def _remision_externa_de(db: Session, ref: Referral) -> RemisionExterna | None:
    return db.scalar(select(RemisionExterna).where(RemisionExterna.referral_id == ref.id, RemisionExterna.direccion == "entrante"))


async def _avisar_socio(rx_id) -> dict | None:
    """Tras la decisión del especialista, el Task vuelve solo al socio. Si el socio no responde, queda 'fallida' y se reenvía."""
    if rx_id is None:
        return None
    rx = await responder_socio(rx_id)
    return {"remision_externa": f"rem-{rx.numero}", "estado": rx.estado, "envio": rx.respuesta_envio, "http": rx.respuesta_http}


@router.post("/referrals/{referral_id}/accept")
async def accept_referral(referral_id: uuid.UUID, payload: ReferralAccept | None = None, db: Session = Depends(get_db), current_user: User = Depends(require_roles("ESPECIALISTA","ADMIN"))):
    nota=((payload.nota if payload else None) or "").strip() or None
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
    ref.responded_at=datetime.utcnow(); ref.response_note=nota
    rx=_remision_externa_de(db, ref)
    n=None
    if rx is None:
        n=create_notification(db,user_id=ref.referred_by,tipo="remision_aceptada",mensaje="El especialista aceptó la remisión.",patient_id=ref.patient_id,data={"remision_id":str(ref.id)})
    else:
        rx.estado="accepted"; rx.respuesta_motivo=nota; rx.respuesta_envio="pendiente"; rx.actualizado=datetime.now(timezone.utc)
    add_audit(db,user_id=current_user.id,action="REFERRAL_ACCEPTED",entity_type="REFERRAL",entity_id=ref.id,details={"patient_id":str(ref.patient_id),"origen":"externo" if rx else "interno",**({"remision_externa":rx.numero} if rx else {})})
    rx_id=rx.id if rx else None
    db.commit()
    if n is not None:
        db.refresh(n); await publish_notification(n)
    db.close()
    return {"id":str(referral_id),"estado":"aceptada","respuesta_socio":await _avisar_socio(rx_id)}


@router.post("/referrals/{referral_id}/reject")
async def reject_referral(referral_id: uuid.UUID, payload: ReferralReject, db: Session = Depends(get_db), current_user: User = Depends(require_roles("ESPECIALISTA","ADMIN"))):
    motivo=(payload.motivo or payload.reason or "").strip()
    if len(motivo)<5: raise HTTPException(status_code=422, detail="El rechazo necesita un motivo (mínimo 5 caracteres)")
    ref=db.get(Referral, referral_id)
    if ref is None: raise HTTPException(status_code=404, detail="Remisión no encontrada")
    if canonical_role(current_user.role.code)!="admin" and ref.specialist_id!=current_user.id: raise HTTPException(status_code=403, detail="Esta remisión pertenece a otro especialista")
    if ref.status!="PENDING": raise HTTPException(status_code=409, detail="La remisión ya fue resuelta")
    ref.status="REJECTED"; ref.response_note=motivo; ref.responded_at=datetime.utcnow()
    rx=_remision_externa_de(db, ref)
    n=None
    if rx is None:
        n=create_notification(db,user_id=ref.referred_by,tipo="remision_rechazada",mensaje=f"El especialista rechazó la remisión: {motivo[:200]}",patient_id=ref.patient_id,data={"remision_id":str(ref.id)})
    else:
        rx.estado="rejected"; rx.respuesta_motivo=motivo; rx.respuesta_envio="pendiente"; rx.actualizado=datetime.now(timezone.utc)
    add_audit(db,user_id=current_user.id,action="REFERRAL_REJECTED",entity_type="REFERRAL",entity_id=ref.id,details={"patient_id":str(ref.patient_id),"motivo":motivo,"origen":"externo" if rx else "interno",**({"remision_externa":rx.numero} if rx else {})})
    rx_id=rx.id if rx else None
    db.commit()
    if n is not None:
        db.refresh(n); await publish_notification(n)
    db.close()
    return {"id":str(referral_id),"estado":"rechazada","motivo":motivo,"respuesta_socio":await _avisar_socio(rx_id)}
