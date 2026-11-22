"""Lado humano de la interoperabilidad (usuarios con sesión): socios (admin), consentimiento y remisiones externas."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.dependencies.auth import require_roles
from app.interop import socios as reg
from app.interop.servicio import (armar_bundle, consent_fhir, consentimiento_activo, enviar_remision, responder_socio,
                                  ultimo_reporte_aprobado)
from app.models.ai_report import AIReport
from app.models.interop import Consentimiento, RemisionExterna, Socio
from app.models.patient import Patient
from app.models.referral import Referral
from app.models.user import User
from app.services.access import ensure_patient_access
from app.services.audit import add_audit
from app.services.identifiers import resolve_patient_identifier
from app.services.roles import canonical_role

router = APIRouter(tags=["Interoperabilidad (clínicos y admin)"])


def _socio(db: Session, codigo: str) -> Socio:
    s = db.scalar(select(Socio).where(Socio.codigo == codigo.strip().lower()))
    if s is None:
        raise HTTPException(404, f"No existe la institución {codigo}")
    return s


def _audit(db, user, accion, entity_id=None, **det):
    add_audit(db, user_id=user.id, action=accion, entity_type="INTEROP", entity_id=entity_id, details={"origen": "interno", **det})


# ---------- admin: instituciones socias ----------
class SocioCrear(BaseModel):
    codigo: str = Field(min_length=2, max_length=60)
    nombre: str = Field(min_length=2, max_length=150)
    url_base: str | None = None
    con_firma: bool = False
    ruta_bundle: str | None = None
    ruta_task: str | None = None
    ruta_token: str | None = None
    cabecera_api_key: str | None = None


class SocioEditar(BaseModel):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    url_base: str | None = None
    estado: Literal["activo", "inactivo"] | None = None
    ruta_bundle: str | None = None
    ruta_task: str | None = None
    ruta_token: str | None = None
    cabecera_api_key: str | None = None


class SocioSalida(BaseModel):
    auth: Literal["oauth2", "api_key", "bearer", "ninguno"]
    credencial_id: str | None = None
    credencial_secreto: str | None = None
    scope: str | None = None
    firma_secreto: str | None = None


@router.get("/interop/socios")
def listar_socios(db: Session = Depends(get_db), admin: User = Depends(require_roles("ADMIN"))):
    return [reg.resumen(s) for s in db.scalars(select(Socio).order_by(Socio.codigo))]


@router.post("/interop/socios", status_code=201)
def crear_socio(body: SocioCrear, db: Session = Depends(get_db), admin: User = Depends(require_roles("ADMIN"))):
    try:
        s, secretos = reg.crear_socio(db, codigo=body.codigo, nombre=body.nombre, url_base=body.url_base, con_firma=body.con_firma,
                                      ruta_bundle=body.ruta_bundle, ruta_task=body.ruta_task, ruta_token=body.ruta_token,
                                      cabecera=body.cabecera_api_key)
    except ValueError as e:
        raise HTTPException(422, str(e))
    _audit(db, admin, "INTEROP_SOCIO_CREADO", institucion=s.codigo, client_id=s.client_id, firma=s.exige_firma)
    db.commit()
    return {**reg.resumen(s), "credenciales_una_sola_vez": secretos,
            "aviso": "Entregue estas credenciales a la institución por un canal privado. No se vuelven a mostrar."}


@router.put("/interop/socios/{codigo}")
def editar_socio(codigo: str, body: SocioEditar, db: Session = Depends(get_db), admin: User = Depends(require_roles("ADMIN"))):
    s = _socio(db, codigo)
    cambios = body.model_dump(exclude_unset=True)
    try:
        if "url_base" in cambios:
            s.url_base = reg.validar_url(body.url_base)
    except ValueError as e:
        raise HTTPException(422, str(e))
    for campo in ("nombre", "estado", "ruta_bundle", "ruta_task", "ruta_token"):
        if cambios.get(campo):
            setattr(s, campo, cambios[campo] if not campo.startswith("ruta") else "/" + cambios[campo].lstrip("/"))
    if cambios.get("cabecera_api_key"):
        s.cabecera_api_key = cambios["cabecera_api_key"]
    _audit(db, admin, "INTEROP_SOCIO_ACTUALIZADO", institucion=s.codigo, campos=sorted(cambios))
    db.commit()
    return reg.resumen(s)


@router.post("/interop/socios/{codigo}/salida")
def configurar_salida(codigo: str, body: SocioSalida, db: Session = Depends(get_db), admin: User = Depends(require_roles("ADMIN"))):
    s = _socio(db, codigo)
    try:
        reg.configurar_salida(s, auth=body.auth, credencial_id=body.credencial_id, credencial_secreto=body.credencial_secreto,
                              scope=body.scope, firma_secreto=body.firma_secreto)
    except ValueError as e:
        raise HTTPException(422, str(e))
    _audit(db, admin, "INTEROP_SOCIO_SALIDA", institucion=s.codigo, auth=body.auth, firma=bool(body.firma_secreto))
    db.commit()
    return reg.resumen(s)


@router.post("/interop/socios/{codigo}/rotar")
def rotar(codigo: str, db: Session = Depends(get_db), admin: User = Depends(require_roles("ADMIN"))):
    s = _socio(db, codigo)
    secretos = reg.rotar_secreto(s)
    _audit(db, admin, "INTEROP_SOCIO_ROTADO", institucion=s.codigo)
    db.commit()
    return {**reg.resumen(s), "credenciales_una_sola_vez": secretos}


@router.post("/interop/espejo")
def espejo(con_firma: bool = False, db: Session = Depends(get_db), admin: User = Depends(require_roles("ADMIN"))):
    s = reg.crear_espejo(db, con_firma=con_firma)
    _audit(db, admin, "INTEROP_ESPEJO", institucion=s.codigo, firma=con_firma)
    db.commit()
    return reg.resumen(s)


# ---------- clínicos: instituciones, consentimiento ----------
@router.get("/interop/instituciones")
def instituciones(db: Session = Depends(get_db), user: User = Depends(require_roles("MEDICO", "ESPECIALISTA", "ADMIN"))):
    return [{"codigo": s.codigo, "nombre": s.nombre, "lista_para_enviar": reg.resumen(s)["lista_para_enviar"]}
            for s in db.scalars(select(Socio).where(Socio.estado == "activo").order_by(Socio.nombre))]


class ConsentimientoCrear(BaseModel):
    institucion: str
    nota: str | None = Field(default=None, max_length=500)


def _paciente_visible(db: Session, patient_id: str, user: User) -> Patient:
    p = resolve_patient_identifier(db, patient_id)
    if canonical_role(user.role.code) == "paciente":
        from app.models.patient_assignment import PatientAssignment
        propio = db.scalar(select(PatientAssignment.id).where(PatientAssignment.patient_id == p.id, PatientAssignment.user_id == user.id,
                                                              PatientAssignment.active.is_(True)))
        if not propio:
            raise HTTPException(403, "Solo puede gestionar su propio consentimiento")
    else:
        ensure_patient_access(db, user=user, patient_id=p.id)
    return p


def _cons_json(c: Consentimiento, s: Socio) -> dict:
    return {"id": c.id, "institucion": s.codigo, "institucion_nombre": s.nombre, "estado": c.estado, "finalidad": c.finalidad,
            "registrado_en": c.registrado_en, "revocado_en": c.revocado_en, "nota": c.nota}


@router.get("/patients/{patient_id}/consentimientos")
def listar_consentimientos(patient_id: str, formato: Literal["json", "fhir"] = "json", db: Session = Depends(get_db),
                           user: User = Depends(require_roles("MEDICO", "ESPECIALISTA", "ADMIN", "PACIENTE"))):
    p = _paciente_visible(db, patient_id, user)
    filas = db.execute(select(Consentimiento, Socio).join(Socio, Socio.id == Consentimiento.socio_id)
                       .where(Consentimiento.patient_id == p.id).order_by(Consentimiento.registrado_en.desc())).all()
    if formato == "fhir":
        return {"resourceType": "Bundle", "type": "searchset", "total": len(filas),
                "entry": [{"resource": consent_fhir(c, s, f"Patient/{p.patient_code}")} for c, s in filas]}
    return [_cons_json(c, s) for c, s in filas]


@router.post("/patients/{patient_id}/consentimientos", status_code=201)
def registrar_consentimiento(patient_id: str, body: ConsentimientoCrear, db: Session = Depends(get_db),
                             user: User = Depends(require_roles("MEDICO", "ESPECIALISTA", "ADMIN", "PACIENTE"))):
    p = _paciente_visible(db, patient_id, user)
    s = _socio(db, body.institucion)
    previo = consentimiento_activo(db, p.id, s.id)
    if previo:
        return _cons_json(previo, s)
    c = Consentimiento(patient_id=p.id, socio_id=s.id, registrado_por=user.id, nota=body.nota)
    db.add(c)
    db.flush()
    _audit(db, user, "CONSENTIMIENTO_REGISTRADO", patient_id=str(p.id), institucion=s.codigo, consentimiento=c.id)
    db.commit()
    db.refresh(c)
    return _cons_json(c, s)


@router.delete("/patients/{patient_id}/consentimientos/{consentimiento_id}")
def revocar_consentimiento(patient_id: str, consentimiento_id: int, db: Session = Depends(get_db),
                           user: User = Depends(require_roles("MEDICO", "ESPECIALISTA", "ADMIN", "PACIENTE"))):
    from datetime import datetime, timezone
    p = _paciente_visible(db, patient_id, user)
    c = db.get(Consentimiento, consentimiento_id)
    if c is None or c.patient_id != p.id:
        raise HTTPException(404, "Consentimiento no encontrado")
    if c.estado == "activo":
        c.estado, c.revocado_por, c.revocado_en = "revocado", user.id, datetime.now(timezone.utc)
        _audit(db, user, "CONSENTIMIENTO_REVOCADO", patient_id=str(p.id), consentimiento=c.id)
        db.commit()
    return _cons_json(c, db.get(Socio, c.socio_id))


# ---------- clínicos: remisiones externas ----------
class RemisionExternaCrear(BaseModel):
    paciente_id: str
    institucion: str
    motivo: str = Field(min_length=5, max_length=2000)
    prioridad: Literal["routine", "urgent", "asap", "stat"] = "routine"
    especialidad: str | None = Field(default="Medicina interna", max_length=120)  # la que todos los socios atienden
    observaciones: list[str] | None = None  # ids; sin elegir: el último valor de cada examen de 30 días (máx. 20)
    reporte_id: str | None = None           # sin elegir: el último reporte de IA APROBADO


def _rx_json(rx: RemisionExterna, s: Socio, p: Patient | None, ref: Referral | None = None, detalle: bool = False) -> dict:
    d = {"id": str(rx.id), "numero": rx.numero, "codigo": f"rem-{rx.numero}", "direccion": rx.direccion,
         "institucion": s.codigo, "institucion_nombre": s.nombre, "paciente_id": str(rx.patient_id),
         "paciente_codigo": p.patient_code if p else None, "estado": rx.estado, "prioridad": rx.prioridad,
         "especialidad": rx.especialidad, "motivo": rx.motivo, "respuesta_motivo": rx.respuesta_motivo,
         "observaciones": rx.observaciones, "creado": rx.creado, "actualizado": rx.actualizado,
         "remision_id": str(rx.referral_id) if rx.referral_id else None}
    if rx.direccion == "saliente":
        d.update({"estado_envio": rx.estado_envio, "envio_http": rx.envio_http, "id_remoto": rx.id_remoto, "intentos": rx.intentos,
                  "envio_detalle": (rx.envio_detalle or "")[:500] if rx.estado_envio == "fallida" else None})
    else:
        d.update({"respuesta_envio": rx.respuesta_envio, "respuesta_http": rx.respuesta_http,
                  "respuesta_detalle": (rx.respuesta_detalle or "")[:500] if rx.respuesta_envio == "fallida" else None,
                  "reporte_ia": rx.reporte, "consentimiento_recibido": bool(rx.consentimiento),
                  "estado_remision": ref.status.lower() if ref else None})
    if detalle:
        d["bundle"] = rx.bundle
    return d


@router.post("/remisiones/externas", status_code=201)
async def remitir_externa(body: RemisionExternaCrear, db: Session = Depends(get_db), user: User = Depends(require_roles("MEDICO"))):
    p = resolve_patient_identifier(db, body.paciente_id)
    ensure_patient_access(db, user=user, patient_id=p.id)
    s = _socio(db, body.institucion)
    if not reg.resumen(s)["lista_para_enviar"]:
        raise HTTPException(409, f"La institución {s.codigo} está inactiva o le falta la URL o la credencial de salida")
    cons = consentimiento_activo(db, p.id, s.id)
    if cons is None:
        raise HTTPException(409, f"El paciente no tiene consentimiento vigente para compartir con {s.nombre} (Ley 1581 de 2012). Regístrelo primero.")
    if body.reporte_id:
        try:
            rep = db.get(AIReport, uuid.UUID(body.reporte_id))
        except ValueError:
            rep = None
        if rep is None or rep.patient_id != p.id:
            raise HTTPException(404, "Reporte de IA no encontrado para este paciente")
        if rep.status != "APPROVED":
            raise HTTPException(409, "Solo se envía un reporte de IA APROBADO; este está pendiente o rechazado")
    else:
        rep = ultimo_reporte_aprobado(db, p.id)
        if rep is None:
            raise HTTPException(409, "El paciente no tiene un reporte de IA aprobado: analícelo y apruébelo antes de remitir")
    rx = RemisionExterna(direccion="saliente", socio_id=s.id, patient_id=p.id, medico_id=user.id, estado="requested",
                         prioridad=body.prioridad, especialidad=(body.especialidad or None), motivo=body.motivo.strip(),
                         estado_envio="pendiente")
    db.add(rx)
    db.flush()
    db.refresh(rx)
    rx.bundle = armar_bundle(db, rx, p, s, cons, rep, body.observaciones)
    rx.observaciones = sum(1 for e in rx.bundle["entry"] if e["resource"]["resourceType"] == "Observation")
    _audit(db, user, "INTEROP_REMISION_CREADA", entity_id=rx.id, institucion=s.codigo, numero=rx.numero,
           patient_id=str(p.id), reporte_id=str(rep.id), consentimiento=cons.id, observaciones=rx.observaciones)
    db.commit()
    rx_id = rx.id
    db.close()  # no retener la conexión mientras se espera al socio
    rx = await enviar_remision(rx_id)
    with SessionLocal() as db2:
        return _rx_json(rx, db2.get(Socio, rx.socio_id), db2.get(Patient, rx.patient_id))


@router.get("/remisiones/externas")
def listar_externas(direccion: Literal["entrante", "saliente"] | None = None, db: Session = Depends(get_db),
                    user: User = Depends(require_roles("MEDICO", "ESPECIALISTA", "ADMIN"))):
    rol = canonical_role(user.role.code)
    q = (select(RemisionExterna, Socio, Patient, Referral).join(Socio, Socio.id == RemisionExterna.socio_id)
         .join(Patient, Patient.id == RemisionExterna.patient_id).outerjoin(Referral, Referral.id == RemisionExterna.referral_id))
    if rol == "medico":
        q = q.where(RemisionExterna.medico_id == user.id)
    elif rol == "especialista":
        q = q.where(Referral.specialist_id == user.id)
    if direccion:
        q = q.where(RemisionExterna.direccion == direccion)
    return [_rx_json(rx, s, p, ref) for rx, s, p, ref in db.execute(q.order_by(RemisionExterna.creado.desc()).limit(200)).all()]


def _rx_autorizada(db: Session, rx_id: str, user: User) -> RemisionExterna:
    try:
        rx = db.get(RemisionExterna, uuid.UUID(rx_id))
    except ValueError:
        rx = None
    if rx is None:
        raise HTTPException(404, "Remisión externa no encontrada")
    rol = canonical_role(user.role.code)
    if rol == "medico" and rx.medico_id != user.id:
        raise HTTPException(403, "Esta remisión es de otro médico")
    if rol == "especialista":
        ref = db.get(Referral, rx.referral_id) if rx.referral_id else None
        if ref is None or ref.specialist_id != user.id:
            raise HTTPException(403, "Esta remisión es de otro especialista")
    return rx


@router.get("/remisiones/externas/{rx_id}")
def detalle_externa(rx_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("MEDICO", "ESPECIALISTA", "ADMIN"))):
    rx = _rx_autorizada(db, rx_id, user)
    return _rx_json(rx, db.get(Socio, rx.socio_id), db.get(Patient, rx.patient_id),
                    db.get(Referral, rx.referral_id) if rx.referral_id else None, detalle=True)


@router.post("/remisiones/externas/{rx_id}/reenviar")
async def reenviar(rx_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("MEDICO", "ESPECIALISTA", "ADMIN"))):
    """Saliente: reenvía el Bundle (el socio deduplica por el identifier del ServiceRequest). Entrante: reenvía nuestra respuesta."""
    rx = _rx_autorizada(db, rx_id, user)
    _audit(db, user, "INTEROP_REENVIO", entity_id=rx.id, numero=rx.numero, direccion=rx.direccion)
    db.commit()
    rid, direccion, estado = rx.id, rx.direccion, rx.estado
    db.close()
    if direccion == "saliente":
        rx = await enviar_remision(rid)
    else:
        if estado == "requested":
            raise HTTPException(409, "El especialista aún no ha respondido esta remisión")
        rx = await responder_socio(rid)
    with SessionLocal() as db2:
        return _rx_json(rx, db2.get(Socio, rx.socio_id), db2.get(Patient, rx.patient_id),
                        db2.get(Referral, rx.referral_id) if rx.referral_id else None)
