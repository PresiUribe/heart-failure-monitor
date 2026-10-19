import io, uuid
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.services.audit import add_audit
from app.models.ai_report import AIReport
from app.models.observation import Observation
from app.models.patient import Patient
from app.models.user import User
from app.services.access import ensure_patient_access
from app.services.identifiers import resolve_patient_identifier

router=APIRouter(tags=["Historia clínica"])

def build_history(db, patient_id):
    p=db.get(Patient,patient_id)
    if p is None: raise HTTPException(status_code=404,detail="Paciente no encontrado")
    obs=list(db.scalars(select(Observation).where(Observation.patient_id==patient_id,Observation.is_active.is_(True),Observation.deleted_at.is_(None)).order_by(Observation.measured_at.desc())))
    reports=list(db.scalars(select(AIReport).where(AIReport.patient_id==patient_id,AIReport.status=="APPROVED").order_by(AIReport.created_at.desc())))
    return p,obs,reports

@router.get("/patients/{patient_id}/history")
def history(patient_id: str, db: Session=Depends(get_db), current_user: User=Depends(get_current_user)):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db,user=current_user,patient_id=patient.id)
    p,obs,reports=build_history(db,patient.id)
    return {"patient":{"id":str(p.id),"patient_code":p.patient_code,"name":f"{p.first_name} {p.last_name}"},"observations":[{"id":str(o.id),"code":o.code,"display":o.display,"value_numeric":float(o.value_numeric) if o.value_numeric is not None else None,"value_text":o.value_text,"unit":o.unit,"measured_at":o.measured_at} for o in obs],"reportes_ia":[{"id":str(r.id),"estado":"aprobado","risk_score":r.risk_score,"nivel":r.level,"observaciones":r.clinical_notes} for r in reports]}

@router.get("/patients/{patient_id}/history.pdf")
def history_pdf(patient_id: str, db: Session=Depends(get_db), current_user: User=Depends(get_current_user)):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db,user=current_user,patient_id=patient.id)
    p,obs,reports=build_history(db,patient.id)
    add_audit(db, user_id=current_user.id, action="HISTORY_PDF_EXPORTED", entity_type="PATIENT", entity_id=patient.id,
              details={"patient_id": str(patient.id), "reportes_aprobados": len(reports)})
    db.commit()
    buff=io.BytesIO(); c=canvas.Canvas(buff,pagesize=letter,pageCompression=0); y=760
    c.setFont("Helvetica-Bold",14); c.drawString(50,y,"Historia clínica - Proyecto académico"); y-=28
    c.setFont("Helvetica",11); c.drawString(50,y,f"Paciente: {p.first_name} {p.last_name} ({p.patient_code})"); y-=22
    c.drawString(50,y,f"Diagnóstico: {p.primary_diagnosis or 'Sin dato'}"); y-=30
    c.setFont("Helvetica-Bold",11); c.drawString(50,y,"Observaciones activas"); y-=18; c.setFont("Helvetica",9)
    for o in obs[:25]:
        text=f"{o.measured_at:%Y-%m-%d} | {o.display or o.code}: {o.value_numeric if o.value_numeric is not None else o.value_text or ''} {o.unit or ''}"
        c.drawString(55,y,text[:105]); y-=14
        if y<90: c.showPage(); y=760
    y-=10; c.setFont("Helvetica-Bold",11); c.drawString(50,y,"Reportes de IA aprobados"); y-=18; c.setFont("Helvetica",9)
    for r in reports:
        c.drawString(55,y,f"{r.created_at:%Y-%m-%d} | riesgo={r.risk_score:.1%} | nivel={r.level} | {r.clinical_notes or ''}"[:105]); y-=14
        if y<90: c.showPage(); y=760
    c.save(); buff.seek(0)
    return StreamingResponse(buff,media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="historia_{p.patient_code}.pdf"'})
