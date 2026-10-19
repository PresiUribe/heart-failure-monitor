import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies.auth import get_current_user, require_roles
from app.models.imaging_study import ImagingStudy
from app.models.patient import Patient
from app.models.user import User
from app.services.access import ensure_patient_access
from app.services.identifiers import resolve_patient_identifier
from app.services.audit import add_audit
from app.services.orthanc_client import image_to_dicom, preview_png, upload_instance

router = APIRouter(tags=["Imágenes / PACS"])

@router.post("/patients/{patient_id}/images/import", status_code=201)
async def import_image(patient_id: str, file: UploadFile = File(...), source_subject_id: str = Form(...), dataset: str = Form("CAMUS"), view: str | None = Form(None), phase: str | None = Form(None), db: Session = Depends(get_db), current_user: User = Depends(require_roles("ADMIN","MEDICO","ESPECIALISTA"))):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db,user=current_user,patient_id=patient.id)
    raw=await file.read()
    if not raw: raise HTTPException(status_code=400,detail="Archivo vacío")
    if (file.filename or "").lower().endswith(".dcm") or file.content_type=="application/dicom":
        dicom=raw
    else:
        dicom=image_to_dicom(raw,file.filename or "camus.png",patient.patient_code,source_subject_id,view,phase)
    orthanc_id=await upload_instance(dicom)
    rec=ImagingStudy(patient_id=patient.id,dataset=dataset,source_subject_id=source_subject_id,modality="US",view=view,phase=phase,orthanc_instance_id=orthanc_id,academic_link=True,source_license="CC BY-NC-SA 4.0 (selected CAMUS distribution)")
    db.add(rec); db.flush()
    add_audit(db,user_id=current_user.id,action="IMAGE_LINKED",entity_type="IMAGING_STUDY",entity_id=rec.id,details={"patient_id":str(patient.id),"dataset":dataset,"source_subject_id":source_subject_id,"academic_link":True})
    db.commit(); db.refresh(rec)
    return {"id":str(rec.id),"patient_id":str(patient.id),"dataset":rec.dataset,"source_subject_id":rec.source_subject_id,"academic_link":True,"url":f"/images/{rec.id}"}

@router.get("/patients/{patient_id}/images")
def list_images(patient_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    patient = resolve_patient_identifier(db, patient_id)
    ensure_patient_access(db,user=current_user,patient_id=patient.id)
    rows=list(db.scalars(select(ImagingStudy).where(ImagingStudy.patient_id==patient.id).order_by(ImagingStudy.created_at.desc())))
    return [{"id":str(x.id),"patient_id":str(x.patient_id),"dataset":x.dataset,"source_subject_id":x.source_subject_id,"modality":x.modality,"view":x.view,"phase":x.phase,"academic_link":x.academic_link,"url":f"/images/{x.id}"} for x in rows]

@router.get("/images/{image_id}")
async def get_image(image_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rec=db.get(ImagingStudy,image_id)
    if rec is None: raise HTTPException(status_code=404,detail="Imagen no encontrada")
    ensure_patient_access(db,user=current_user,patient_id=rec.patient_id)
    data=await preview_png(rec.orthanc_instance_id)
    return Response(content=data,media_type="image/png",headers={"Content-Disposition":f'inline; filename="{image_id}.png"'})
