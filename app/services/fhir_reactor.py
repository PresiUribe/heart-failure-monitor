from __future__ import annotations

import uuid

from sqlalchemy import or_, select

from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.patient import Patient
from app.models.patient_assignment import PatientAssignment
from app.models.user import User
from app.services.audit import add_audit
from app.services.notifications import create_notification, publish_notification
from app.services.roles import canonical_role


async def react_to_observation(resource: dict) -> dict:
    """React to a FHIR Observation without polling.

    The same reactor is used by the normal HAPI Subscription callback and by
    the public FHIR gateway used during the academic demo. A resource-id based
    guard avoids duplicate user notifications when both paths observe the same
    POST.
    """
    subject_obj = resource.get("subject") or {}
    subject = subject_obj.get("reference", "") or ""
    fhir_patient_id = subject.split("/")[-1] if subject else None
    subject_identifier = (subject_obj.get("identifier") or {}).get("value")
    if not fhir_patient_id and not subject_identifier:
        return {"received": True, "matched": False, "reason": "missing subject"}

    coding = ((resource.get("code") or {}).get("coding") or [{}])[0]
    code = coding.get("code")
    resource_id = resource.get("id")
    notifications = []

    with SessionLocal() as db:
        # HAPI rest-hook and the deterministic public gateway can both observe
        # the same resource. Only notify once when the HAPI resource id exists.
        if resource_id:
            already = db.scalar(
                select(AuditLog.id).where(
                    AuditLog.action == "FHIR_EVENT_RECEIVED",
                    AuditLog.details["resource"].astext == str(resource_id),
                ).limit(1)
            )
            if already:
                return {"received": True, "matched": True, "duplicate": True}

        # El subject puede venir como Patient/<id HAPI> (lo normal), como
        # Patient/<UUID local>, por documento/código o con subject.identifier.
        conditions = []
        for value in {v for v in (fhir_patient_id, subject_identifier) if v}:
            conditions += [
                Patient.fhir_patient_id == value,
                Patient.document_number == value,
                Patient.patient_code == value,
            ]
            try:
                conditions.append(Patient.id == uuid.UUID(str(value)))
            except ValueError:
                pass
        patient = db.scalar(select(Patient).where(or_(*conditions)).limit(1))
        if patient is None:
            return {"received": True, "matched": False, "reason": "patient not mapped"}

        rows = db.execute(
            select(PatientAssignment.user_id, User)
            .join(User, User.id == PatientAssignment.user_id)
            .where(
                PatientAssignment.patient_id == patient.id,
                PatientAssignment.active.is_(True),
            )
        ).all()

        seen = set()
        for uid, user in rows:
            if uid in seen:
                continue
            seen.add(uid)
            if canonical_role(user.role.code) in {"medico", "especialista"}:
                notifications.append(
                    create_notification(
                        db,
                        user_id=uid,
                        tipo="fhir_observation",
                        mensaje=f"Nueva Observation FHIR ({coding.get('display') or code or 'sin código'}) para el paciente {patient.patient_code}.",
                        patient_id=patient.id,
                        data={"origen": "FHIR", "code": code, "fhir_resource_id": resource_id},
                    )
                )

        add_audit(
            db,
            user_id=None,
            action="FHIR_EVENT_RECEIVED",
            entity_type="OBSERVATION",
            entity_id=patient.id,
            details={
                "origin": "FHIR",
                "patient_id": str(patient.id),
                "fhir_patient_id": fhir_patient_id,
                "resource": resource_id,
                "code": code,
            },
        )
        db.commit()
        for n in notifications:
            db.refresh(n)

    for n in notifications:
        await publish_notification(n)

    return {"received": True, "matched": bool(notifications), "duplicate": False}
