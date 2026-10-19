import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.dependencies.auth import require_roles
from app.models.ai_report import AIReport, MLPayload
from app.models.alert import Alert
from app.models.patient import Patient
from app.models.user import User
from app.schemas.corte2 import AIAnalyzeRequest, AIReviewRequest, AlertAcknowledgeRequest
from app.services.access import ensure_patient_access
from app.services.identifiers import resolve_patient_identifier
from app.services.audit import add_audit
from app.services.ml_client import get_clusters, get_metrics, predict
from app.services.mongo import create_invoice
from app.services.notifications import create_notification, publish_notification, role_user_ids
from app.services.pseudonym import patient_pseudonym

router = APIRouter(tags=["IA clínica"])

# El contrato exige los estados en español.
ESTADOS = {"PENDING": "pendiente", "APPROVED": "aprobado", "REJECTED": "rechazado"}


def estado(status: str) -> str:
    return ESTADOS.get(str(status).upper(), str(status).lower())

@router.post("/patients/{patient_id}/ai/analyze", status_code=201)
async def analyze(patient_id: str, payload: AIAnalyzeRequest, db: Session = Depends(get_db), current_user: User = Depends(require_roles("MEDICO","ESPECIALISTA"))):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db,user=current_user,patient_id=patient.id)
    pseudo=patient_pseudonym(str(patient.id))
    model_payload={"pseudonym":pseudo, **payload.model_dump(exclude={"image_id"})}
    if payload.image_id: model_payload["image_id"]=str(payload.image_id)
    result=await predict(model_payload)
    report=AIReport(patient_id=patient.id,created_by=current_user.id,status="PENDING",model_name=result.get("model","uci-heart-failure-logistic"),risk_score=float(result["risk_score"]),level=result.get("level","LOW"),result=result)
    db.add(report); db.flush()
    db.add(MLPayload(report_id=report.id,pseudonym=pseudo,payload=model_payload))
    notifications=[]
    add_audit(db,user_id=current_user.id,action="AI_ANALYSIS",entity_type="AI_REPORT",entity_id=report.id,details={"patient_id":str(patient.id),"risk_score":report.risk_score,"level":report.level})
    alert=None
    if str(report.level).upper()=="CRITICAL" or report.risk_score>=settings.critical_risk_threshold:
        alert=Alert(patient_id=patient.id,report_id=report.id,assigned_to=current_user.id,level="CRITICAL",message=f"Caso crítico: riesgo estimado {report.risk_score:.1%}")
        db.add(alert); db.flush()
        notifications.append(create_notification(db,user_id=current_user.id,tipo="alerta_critica",mensaje=alert.message,patient_id=patient.id,data={"alerta_id":str(alert.id),"reporte_id":str(report.id)}))
        add_audit(db,user_id=current_user.id,action="CRITICAL_ALERT_CREATED",entity_type="ALERT",entity_id=alert.id,details={"patient_id":str(patient.id),"report_id":str(report.id)})
    db.commit(); db.refresh(report)
    for n in notifications:
        db.refresh(n); await publish_notification(n)
    return {"id":str(report.id),"estado":"pendiente","risk_score":report.risk_score,"nivel":report.level,"alerta_id":str(alert.id) if alert else None}

@router.get("/patients/{patient_id}/ai/reports")
def list_patient_reports(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("MEDICO", "ESPECIALISTA", "ADMIN")),
):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db, user=current_user, patient_id=patient.id)
    rows = list(
        db.scalars(
            select(AIReport)
            .where(AIReport.patient_id == patient.id)
            .order_by(AIReport.created_at.desc())
        )
    )
    return [
        {
            "id": str(r.id),
            "paciente_id": str(r.patient_id),
            "estado": estado(r.status),
            "risk_score": r.risk_score,
            "nivel": r.level,
            "resultado": r.result,
            "observaciones": r.clinical_notes,
            "fecha": r.created_at,
        }
        for r in rows
    ]


@router.get("/ai/reports/{report_id}")
def get_report(report_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(require_roles("MEDICO","ESPECIALISTA","ADMIN"))):
    r=db.get(AIReport,report_id)
    if r is None: raise HTTPException(status_code=404,detail="Reporte no encontrado")
    ensure_patient_access(db,user=current_user,patient_id=r.patient_id)
    return {"id":str(r.id),"paciente_id":str(r.patient_id),"estado":estado(r.status),"risk_score":r.risk_score,"nivel":r.level,"resultado":r.result,"observaciones":r.clinical_notes}

@router.get("/ai/reports/{report_id}/payload")
def get_payload(report_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(require_roles("MEDICO","ESPECIALISTA","ADMIN"))):
    r=db.get(AIReport,report_id)
    if r is None: raise HTTPException(status_code=404,detail="Reporte no encontrado")
    ensure_patient_access(db,user=current_user,patient_id=r.patient_id)
    p=db.scalar(select(MLPayload).where(MLPayload.report_id==report_id))
    return {"payload":p.payload if p else {}}

@router.post("/ai/reports/{report_id}/approve")
async def approve(report_id: uuid.UUID, payload: AIReviewRequest | None = None, db: Session = Depends(get_db), current_user: User = Depends(require_roles("MEDICO","ESPECIALISTA"))):
    r=db.get(AIReport,report_id)
    if r is None: raise HTTPException(status_code=404,detail="Reporte no encontrado")
    ensure_patient_access(db,user=current_user,patient_id=r.patient_id)
    if r.status!="PENDING": raise HTTPException(status_code=409,detail="El reporte ya fue revisado")
    r.status="APPROVED"; r.clinical_notes=(payload.observations if payload else None); r.reviewed_by=current_user.id; r.reviewed_at=datetime.utcnow()
    add_audit(db,user_id=current_user.id,action="AI_REPORT_APPROVED",entity_type="AI_REPORT",entity_id=r.id,details={"patient_id":str(r.patient_id)})
    db.commit()
    invoice=create_invoice(patient_id=str(r.patient_id),service="Análisis de IA - insuficiencia cardiaca",value=85000,created_by=str(current_user.id),reporte_id=str(r.id))

    # R19: aprobar IA crea la factura sin un paso manual y la ventana del
    # contable recibe un evento en vivo. La factura vive en MongoDB; la
    # notificación persistente y la auditoría viven en PostgreSQL.
    invoice_notifications=[]
    for accounting_id in role_user_ids(db, "contable"):
        invoice_notifications.append(
            create_notification(
                db,
                user_id=accounting_id,
                tipo="factura_automatica",
                mensaje="Se generó automáticamente una factura por un reporte de IA aprobado.",
                patient_id=r.patient_id,
                data={"reporte_id":str(r.id),"factura_id":str(invoice.get("id"))},
            )
        )
    add_audit(
        db,
        user_id=current_user.id,
        action="INVOICE_CREATED",
        entity_type="INVOICE",
        entity_id=r.id,
        details={"patient_id":str(r.patient_id),"report_id":str(r.id),"invoice_id":str(invoice.get("id")),"automatic":True},
    )
    db.commit()
    for n in invoice_notifications:
        db.refresh(n)
        await publish_notification(n)
    return {"id":str(r.id),"estado":"aprobado","factura":invoice}

@router.post("/ai/reports/{report_id}/reject")
def reject(report_id: uuid.UUID, payload: AIReviewRequest | None = None, db: Session = Depends(get_db), current_user: User = Depends(require_roles("MEDICO","ESPECIALISTA"))):
    r=db.get(AIReport,report_id)
    if r is None: raise HTTPException(status_code=404,detail="Reporte no encontrado")
    ensure_patient_access(db,user=current_user,patient_id=r.patient_id)
    if r.status!="PENDING": raise HTTPException(status_code=409,detail="El reporte ya fue revisado")
    r.status="REJECTED"; r.clinical_notes=(payload.observations if payload else None); r.reviewed_by=current_user.id; r.reviewed_at=datetime.utcnow()
    add_audit(db,user_id=current_user.id,action="AI_REPORT_REJECTED",entity_type="AI_REPORT",entity_id=r.id,details={"patient_id":str(r.patient_id)})
    db.commit(); return {"id":str(r.id),"estado":"rechazado"}

@router.get("/alerts")
def list_alerts(db: Session = Depends(get_db), current_user: User = Depends(require_roles("MEDICO","ESPECIALISTA","ADMIN"))):
    q=select(Alert)
    if current_user.role.code!="ADMIN": q=q.where(Alert.assigned_to==current_user.id)
    rows=list(db.scalars(q.order_by(Alert.created_at.desc())))
    return [{"id":str(a.id),"paciente_id":str(a.patient_id),"nivel":a.level.lower(),"estado":"activa" if a.state=="ACTIVE" else "reconocida","mensaje":a.message,"escalated":a.escalated} for a in rows]

@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: uuid.UUID, payload: AlertAcknowledgeRequest | None = None, db: Session = Depends(get_db), current_user: User = Depends(require_roles("MEDICO","ESPECIALISTA"))):
    a=db.get(Alert,alert_id)
    if a is None: raise HTTPException(status_code=404,detail="Alerta no encontrada")
    if a.assigned_to!=current_user.id: raise HTTPException(status_code=403,detail="Alerta asignada a otro clínico")
    payload = payload or AlertAcknowledgeRequest()
    if a.state!="ACTIVE": return {"id":str(a.id),"estado":"reconocida"}
    a.state="ACKNOWLEDGED"; a.acknowledged_by=current_user.id; a.acknowledgment_note=payload.note; a.acknowledged_at=datetime.utcnow()
    add_audit(db,user_id=current_user.id,action="CRITICAL_ALERT_ACKNOWLEDGED",entity_type="ALERT",entity_id=a.id,details={"patient_id":str(a.patient_id),"note":payload.note})
    db.commit(); return {"id":str(a.id),"estado":"reconocida"}

@router.get("/clusters")
async def clusters(current_user: User = Depends(require_roles("MEDICO","ESPECIALISTA","ADMIN"))):
    return await get_clusters()

@router.get("/ml/metrics")
async def metrics(current_user: User = Depends(require_roles("MEDICO","ESPECIALISTA","ADMIN"))):
    return await get_metrics()
