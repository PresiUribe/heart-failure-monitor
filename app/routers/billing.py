from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import require_roles
from app.models.user import User
from app.services.identifiers import resolve_patient_identifier
from app.schemas.corte2 import InvoiceCreate
from app.services.audit import add_audit
from app.services.mongo import billing_collection, create_invoice

router = APIRouter(prefix="/billing", tags=["Contabilidad"])


def serialize(doc):
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    d.setdefault("paciente_id", d.get("patient_id"))
    return d


@router.post("/invoices", status_code=201)
def create_manual_invoice(
    payload: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("CONTABLE", "ADMIN")),
):
    patient = resolve_patient_identifier(db, payload.patient_id)
    invoice = create_invoice(
        patient_id=str(patient.id),
        service=payload.service,
        value=payload.value,
        created_by=str(current_user.id),
    )
    # Mongo ObjectIds are not UUIDs, so entity_id remains nullable and the exact
    # invoice id is captured in details.
    add_audit(
        db,
        user_id=current_user.id,
        action="INVOICE_CREATED",
        entity_type="INVOICE",
        entity_id=None,
        details={
            "patient_id": str(patient.id),
            "patient_document_number": patient.document_number,
            "invoice_id": invoice["id"],
            "automatic": False,
        },
    )
    db.commit()
    return invoice


@router.get("/invoices")
def list_invoices(current_user: User = Depends(require_roles("CONTABLE", "ADMIN"))):
    return [serialize(x) for x in billing_collection().find({}).sort("fecha", -1).limit(300)]
