"""Lógica de la interoperabilidad: recibir y enviar remisiones, responder con Task, consentimiento y auditoría."""
from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.interop import cripto
from app.interop.fhir import (InteropError, RemisionEntrante, entrada, especialidad_fhir, iso, motivo_task,
                              observacion_saliente, referencias_task, sistema, task_de_entrante, VARIABLES_IA, SNOMED)
from app.models.ai_report import AIReport, MLPayload
from app.models.interop import Consentimiento, RemisionExterna, Socio
from app.models.observation import Observation
from app.models.observation_component import ObservationComponent
from app.models.patient import Patient
from app.models.referral import Referral
from app.models.role import Role
from app.models.user import User
from app.security.campos import indice_ciego
from app.security.password import hash_password
from app.services.audit import add_audit
from app.services.notifications import admin_ids, create_notification, publish_notification
from app.services.roles import canonical_role

log = logging.getLogger(__name__)
USUARIO_SISTEMA = "sistema.interop"
_tokens: dict[str, tuple[str, float]] = {}


def ahora() -> datetime:
    return datetime.now(timezone.utc)


# ---------- auditoría (origen externo) ----------
def auditar(db: Session, accion: str, *, socio: Socio | None, user_id=None, entity_id=None, **detalles) -> None:
    datos = {"origen": "externo", **({"institucion": socio.codigo, "client_id": socio.client_id} if socio else {}), **detalles}
    add_audit(db, user_id=user_id, action=accion, entity_type="INTEROP", entity_id=entity_id, details=datos)


async def auditar_rechazo(request: Request, err: InteropError) -> None:
    """Se escribe en una sesión aparte: el rechazo queda aunque la transacción principal se deshaga."""
    socio_codigo = getattr(request.state, "interop_socio", None)
    detalles = {"origen": "externo", "ruta": request.url.path, "http": err.status, "ip": _ip(request),
                "client_id": getattr(request.state, "interop_client_id", None), "institucion": socio_codigo,
                "problemas": [d for _, d in err.issues][:20]}
    avisos = []
    try:
        with SessionLocal() as s:
            add_audit(s, user_id=None, action="INTEROP_RECHAZO", entity_type="INTEROP", entity_id=None, details=detalles)
            # Credencial mala o duplicado: solo auditoría. El resto avisa al admin en vivo.
            if err.status not in (401, 409):
                for aid in admin_ids(s):
                    avisos.append(create_notification(
                        s, user_id=aid, tipo="interop_rechazo",
                        mensaje=f"Rechazo {err.status} en {request.url.path} ({socio_codigo or 'sin institución'}): {err.issues[0][1][:200]}",
                        data={"http": err.status, "institucion": socio_codigo}))
            s.commit()
            for n in avisos:
                s.refresh(n)
        for n in avisos:
            await publish_notification(n)
    except Exception as exc:  # la auditoría nunca debe tumbar la respuesta al socio
        log.warning("No se pudo auditar el rechazo interop: %s", exc)


def _ip(request: Request) -> str:
    from app.interop.seguridad import ip_de
    return ip_de(request)


# ---------- usuarios y pacientes ----------
def usuario_sistema(db: Session) -> User:
    """Usuario técnico INACTIVO (no puede iniciar sesión) que figura como autor de lo que llega de fuera.

    Se crea al primer uso, no al arrancar, para no romper /setup/bootstrap-admin en una base nueva.
    """
    u = db.scalar(select(User).where(User.username == USUARIO_SISTEMA))
    if u:
        return u
    rol = db.scalar(select(Role).where(Role.code == "MEDICO"))
    u = User(document_type="CC", document_number="SYS-INTEROP", full_name="Sistema de interoperabilidad",
             username=USUARIO_SISTEMA, email="sistema.interop@hfmonitor.uao.edu.co",
             password_hash=hash_password(secrets.token_urlsafe(48)), role_id=rol.id, is_active=False)
    db.add(u)
    db.flush()
    return u


def asegurar_sid(db: Session) -> None:
    for p in db.scalars(select(Patient).where(Patient.sid_federado.is_(None))):
        if not p.document_number.startswith("EXT-"):
            p.sid_federado = cripto.sid_federado(p.document_type or "CC", p.document_number)
    db.flush()


def _buscar_o_crear_paciente(db: Session, socio: Socio, rem: RemisionEntrante) -> tuple[Patient, bool]:
    pe = rem.paciente
    asegurar_sid(db)
    sid = pe.federado or (cripto.sid_federado(pe.tipo_doc, pe.documento) if pe.documento else None)
    paciente = None
    if sid:
        candidatos = list(db.scalars(select(Patient).where(Patient.sid_federado == sid, Patient.deleted_at.is_(None))))
        if len(candidatos) == 1:
            paciente = candidatos[0]
    if paciente is None and pe.documento:
        paciente = db.scalar(select(Patient).where(Patient.document_hash == indice_ciego(pe.documento)))
    if paciente is None and not pe.documento:
        seud = (pe.federado or pe.ref_externa or uuid.uuid4().hex)[:32]
        paciente = db.scalar(select(Patient).where(Patient.document_hash == indice_ciego(f"EXT-{socio.codigo}-{seud}"[:50])))
    if paciente is not None:
        return paciente, False
    seud = (pe.federado or pe.ref_externa or uuid.uuid4().hex)[:32]
    paciente = Patient(
        document_type=pe.tipo_doc or "CC",
        document_number=pe.documento or f"EXT-{socio.codigo}-{seud}"[:50],
        first_name=(pe.nombre or "Paciente")[:100],
        last_name=(pe.apellido or f"remitido por {socio.nombre}")[:100],
        birth_date=pe.nacimiento, sex=pe.genero,
        primary_diagnosis=None, sid_federado=sid, origen_institucion=socio.codigo,
    )
    db.add(paciente)
    db.flush()
    return paciente, True


def elegir_especialista(db: Session) -> User | None:
    """El especialista activo con menos remisiones pendientes (empate: el más antiguo)."""
    abiertas = (select(Referral.specialist_id, func.count().label("n")).where(Referral.status == "PENDING")
                .group_by(Referral.specialist_id).subquery())
    filas = db.execute(
        select(User, func.coalesce(abiertas.c.n, 0)).join(Role, Role.id == User.role_id)
        .outerjoin(abiertas, abiertas.c.specialist_id == User.id)
        .where(User.is_active.is_(True), User.deleted_at.is_(None), User.is_locked.is_(False))
        .order_by(func.coalesce(abiertas.c.n, 0), User.created_at)
    ).all()
    return next((u for u, _ in filas if canonical_role(u.role.code) == "especialista"), None)


# ---------- recibir (ellos -> nosotros) ----------
def procesar_bundle(db: Session, socio: Socio, rem: RemisionEntrante, ip: str):
    """Todo en una transacción: si algo falla, el llamador hace rollback y no queda nada."""
    previa = db.scalar(select(RemisionExterna).where(RemisionExterna.socio_id == socio.id,
                                                     RemisionExterna.direccion == "entrante",
                                                     RemisionExterna.clave_externa == rem.clave))
    if previa:
        raise InteropError(409, f"Esa remisión ya fue recibida (nuestro Task rem-{previa.numero})", code="duplicate",
                           cabeceras={"Location": f"/interop/Task/rem-{previa.numero}"})
    especialista = elegir_especialista(db)
    if especialista is None:
        raise InteropError(409, "No hay especialistas activos para recibir la remisión", code="business-rule")
    sistema_u = usuario_sistema(db)
    paciente, nuevo = _buscar_o_crear_paciente(db, socio, rem)

    texto = f"[{socio.nombre}] {rem.motivo}"
    if rem.especialidad:
        texto += f" · Especialidad solicitada: {rem.especialidad}"
    ref = Referral(patient_id=paciente.id, referred_by=sistema_u.id, specialist_id=especialista.id, reason=texto[:4000])
    db.add(ref)
    db.flush()
    for o in rem.observaciones:
        obs = Observation(patient_id=paciente.id, created_by=sistema_u.id, code_system="http://loinc.org", code=o.codigo,
                          display=(o.nombre or o.codigo)[:255], value_type=o.tipo, value_numeric=o.valor,
                          value_text=o.texto, value_boolean=o.booleano, unit=(o.unidad or None) and o.unidad[:50],
                          measured_at=o.fecha or datetime.utcnow(), origen=f"externo:{socio.codigo}"[:80])
        db.add(obs)
        db.flush()
        for c in o.componentes:
            db.add(ObservationComponent(observation_id=obs.id, code_system=c.get("system"), code=str(c["code"])[:100],
                                        display=(c.get("display") or None), value_numeric=c["valor"],
                                        unit=(c.get("unidad") or None)))
    rx = RemisionExterna(direccion="entrante", socio_id=socio.id, patient_id=paciente.id, referral_id=ref.id,
                         estado="requested", prioridad=rem.prioridad, especialidad=rem.especialidad, motivo=rem.motivo,
                         clave_externa=rem.clave, sr_id_externo=rem.sr_id, sr_identifier=rem.sr_identifier,
                         task_id_externo=rem.task_id, patient_ref_externo=rem.paciente.ref_externa, reporte=rem.reporte,
                         consentimiento=rem.consentimiento, observaciones=len(rem.observaciones), bundle=rem.bundle_minimo)
    db.add(rx)
    db.flush()
    db.refresh(rx)
    n = create_notification(
        db, user_id=especialista.id, tipo="remision_externa_recibida",
        mensaje=f"Remisión de {socio.nombre} ({rem.prioridad}) para {paciente.patient_code}: {rem.motivo[:200]}",
        patient_id=paciente.id, data={"remision_id": str(ref.id), "remision_externa": f"rem-{rx.numero}",
                                      "institucion": socio.codigo, "prioridad": rem.prioridad})
    auditar(db, "INTEROP_REMISION_RECIBIDA", socio=socio, entity_id=rx.id, ip=ip, numero=rx.numero,
            remision_id=str(ref.id), patient_id=str(paciente.id), paciente_nuevo=nuevo,
            especialista_id=str(especialista.id), observaciones=len(rem.observaciones),
            reporte_ia=bool(rem.reporte), consentimiento=bool(rem.consentimiento), clave_externa=rem.clave)
    return rx, n


def procesar_task(db: Session, socio: Socio, task: Any, ip: str):
    """Un socio responde (acepta/rechaza) una remisión que NOSOTROS le enviamos."""
    if not isinstance(task, dict) or task.get("resourceType") != "Task":
        raise InteropError(422, "resourceType debe ser Task")
    estado = task.get("status")
    validos = {"accepted", "rejected", "in-progress", "completed", "cancelled", "failed", "requested", "received"}
    if estado not in validos:
        raise InteropError(422, f"Task.status debe ser uno de {sorted(validos)}")
    numeros, crudos = referencias_task(task)
    if not numeros and not crudos:
        raise InteropError(422, "El Task debe señalar nuestra remisión: focus/basedOn a ServiceRequest/rem-<n>, su identifier, o id rem-<n>", code="required")
    rx = None
    if numeros:
        rx = db.scalar(select(RemisionExterna).where(RemisionExterna.numero.in_(numeros), RemisionExterna.socio_id == socio.id,
                                                     RemisionExterna.direccion == "saliente"))
    if rx is None and crudos:
        rx = db.scalar(select(RemisionExterna).where(RemisionExterna.id_remoto.in_(crudos), RemisionExterna.socio_id == socio.id,
                                                     RemisionExterna.direccion == "saliente"))
    if rx is None:
        raise InteropError(404, "No corresponde a ninguna remisión que les hayamos enviado", code="not-found")
    motivo = motivo_task(task)
    if estado in ("requested", "received"):
        return rx, None  # acuse de recibo: nada que avisar
    if rx.estado == estado and (rx.respuesta_motivo or None) == motivo:
        return rx, None  # reintento: mismo estado, no se repite el aviso
    rx.estado, rx.respuesta_motivo, rx.actualizado = estado, motivo, ahora()
    if task.get("id") and not rx.id_remoto:
        rx.id_remoto = str(task["id"])[:200]
    paciente = db.get(Patient, rx.patient_id)
    verbo = {"accepted": "aceptó", "rejected": "rechazó", "completed": "completó", "in-progress": "está atendiendo",
             "cancelled": "canceló", "failed": "marcó como fallida"}[estado]
    n = None
    if rx.medico_id:
        n = create_notification(
            db, user_id=rx.medico_id, tipo="remision_externa_respondida",
            mensaje=f"{socio.nombre} {verbo} la remisión rem-{rx.numero} de {paciente.patient_code}" + (f": {motivo[:200]}" if motivo else ""),
            patient_id=rx.patient_id, data={"remision_externa": f"rem-{rx.numero}", "estado": estado, "institucion": socio.codigo})
    auditar(db, "INTEROP_RESPUESTA_RECIBIDA", socio=socio, entity_id=rx.id, ip=ip, numero=rx.numero, estado=estado,
            motivo=motivo, patient_id=str(rx.patient_id))
    return rx, n


# ---------- consentimiento ----------
def consentimiento_activo(db: Session, patient_id, socio_id) -> Consentimiento | None:
    return db.scalar(select(Consentimiento).where(Consentimiento.patient_id == patient_id, Consentimiento.socio_id == socio_id,
                                                  Consentimiento.estado == "activo"))


def consent_fhir(c: Consentimiento, socio: Socio, pac_ref: str) -> dict:
    r = {
        "resourceType": "Consent", "id": f"con-{c.id}", "status": "active" if c.estado == "activo" else "inactive",
        "scope": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/consentscope", "code": "patient-privacy"}]},
        "category": [{"coding": [{"system": "http://loinc.org", "code": "59284-0", "display": "Patient Consent"}]}],
        "patient": {"reference": pac_ref}, "dateTime": iso(c.registrado_en),
        "organization": [{"display": settings.interop_nombre}],
        "policyRule": {"text": "Ley 1581 de 2012: autorización para transferir datos a la institución destino"},
        "provision": {"type": "permit", "period": {"start": iso(c.registrado_en)},
                      "actor": [{"role": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ParticipationType", "code": "IRCP", "display": "information recipient"}]},
                                 "reference": {"display": socio.nombre}}],
                      "purpose": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ActReason", "code": c.finalidad}]},
    }
    if c.revocado_en:
        r["provision"]["period"]["end"] = iso(c.revocado_en)
    return r


# ---------- enviar (nosotros -> ellos) ----------
def ultimo_reporte_aprobado(db: Session, patient_id) -> AIReport | None:
    return db.scalar(select(AIReport).where(AIReport.patient_id == patient_id, AIReport.status == "APPROVED")
                     .order_by(AIReport.reviewed_at.desc().nullslast(), AIReport.created_at.desc()))


def _observaciones_para_enviar(db: Session, paciente: Patient, elegidas: list[str] | None) -> list[Observation]:
    q = select(Observation).where(Observation.patient_id == paciente.id, Observation.is_active.is_(True),
                                  Observation.deleted_at.is_(None), Observation.code_system == "http://loinc.org")
    if elegidas:
        ids = []
        for x in elegidas:
            try:
                ids.append(uuid.UUID(str(x)))
            except ValueError:
                raise HTTPException(422, f"Observación {x} no es un id válido")
        filas = list(db.scalars(q.where(Observation.id.in_(ids))))
        if len(filas) != len(set(ids)):
            raise HTTPException(422, "Alguna observación elegida no existe, no es de este paciente o no tiene LOINC")
        return filas[:20]
    desde = datetime.utcnow() - timedelta(days=30)
    ultimas: dict[str, Observation] = {}
    for o in db.scalars(q.where(Observation.measured_at >= desde).order_by(Observation.measured_at.desc())):
        ultimas.setdefault(o.code, o)
    return list(ultimas.values())[:20]


def armar_bundle(db: Session, rx: RemisionExterna, paciente: Patient, socio: Socio, cons: Consentimiento,
                 reporte: AIReport, elegidas: list[str] | None) -> dict:
    asegurar_sid(db)
    pac_id = f"pac-{paciente.sid_federado or paciente.id.hex[:32]}"
    pac_ref = f"Patient/{pac_id}"
    identificadores = []
    if not paciente.document_number.startswith("EXT-"):
        identificadores.append({"use": "official",
                                "type": {"coding": [{"system": f"{settings.interop_sistema_base}/CodeSystem/tipo-documento", "code": paciente.document_type or "CC"}],
                                         "text": paciente.document_type or "CC"},
                                "system": sistema("documento"), "value": paciente.document_number})
    if paciente.sid_federado:
        identificadores.append({"use": "secondary", "system": sistema("paciente-federado"), "value": paciente.sid_federado})
    patient = {"resourceType": "Patient", "id": pac_id, "identifier": identificadores,
               "name": [{"use": "official", "family": paciente.last_name, "given": [paciente.first_name]}]}
    if paciente.sex:
        patient["gender"] = paciente.sex.lower()
    if paciente.birth_date:
        patient["birthDate"] = paciente.birth_date.isoformat()
    # Minimización (R32): ni teléfono, ni correo, ni dirección, ni municipio/vereda.

    observaciones = []
    codigos = set()
    for o in _observaciones_para_enviar(db, paciente, elegidas):
        codigos.add(o.code)
        comps = [{"code": c.code, "display": c.display, "valor": float(c.value_numeric), "unidad": c.unit} for c in o.components] if o.value_type == "COMPONENTS" else None
        observaciones.append(observacion_saliente(
            f"obs-{o.id}", pac_ref, o.code, o.display or o.code, o.measured_at,
            valor=float(o.value_numeric) if o.value_type == "NUMERIC" and o.value_numeric is not None else None,
            unidad=o.unit, texto=o.value_text if o.value_type == "TEXT" else ("Sí" if o.value_boolean else "No") if o.value_type == "BOOLEAN" else None,
            componentes=comps))
    # Las variables de laboratorio con que se calculó el reporte aprobado (si no hay una medición más reciente)
    carga = db.scalar(select(MLPayload).where(MLPayload.report_id == reporte.id))
    for campo, (loinc, nombre, unidad, factor, cat) in VARIABLES_IA.items():
        valor = (carga.payload or {}).get(campo) if carga else None
        if loinc in codigos or not isinstance(valor, (int, float)) or len(observaciones) >= 20:
            continue
        observaciones.append(observacion_saliente(f"obs-ia-{reporte.id.hex[:12]}-{loinc}", pac_ref, loinc, nombre,
                                                  reporte.created_at, valor=round(float(valor) * factor, 3), unidad=unidad, categoria=cat))
    r = reporte.result or {}
    nivel = {"low": "bajo", "medium": "moderado", "moderate": "moderado", "high": "alto", "critical": "crítico"}.get(
        str(reporte.level).lower(), str(reporte.level).lower())
    conclusion = (f"Modelo {reporte.model_name}: riesgo {nivel}, probabilidad {reporte.risk_score:.0%}."
                  + (f" Criterios críticos: {', '.join(map(str, r.get('criterios_criticos') or []))}." if r.get("criterios_criticos") else "")
                  + (f" Revisión clínica: {reporte.clinical_notes}." if reporte.clinical_notes else "")
                  + " Reporte revisado y APROBADO por un clínico.")
    dr_id = f"rep-{reporte.id}"
    diagnostic = {
        "resourceType": "DiagnosticReport", "id": dr_id,
        "identifier": [{"system": sistema("reporte-ia"), "value": str(reporte.id)}],
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "OTH", "display": "Other"}],
                      "text": "Apoyo diagnóstico con IA"}],
        "code": {"text": "Reporte de riesgo de descompensación de insuficiencia cardiaca (IA), aprobado"},
        "subject": {"reference": pac_ref}, "issued": iso(reporte.reviewed_at or reporte.created_at),
        "result": [{"reference": f"Observation/{o['id']}"} for o in observaciones],
        "conclusion": conclusion,
    }
    consent = consent_fhir(cons, socio, pac_ref)
    sr_id = f"rem-{rx.numero}"
    service_request = {
        "resourceType": "ServiceRequest", "id": sr_id,
        "identifier": [{"system": sistema("remision"), "value": str(rx.numero)}],
        "status": "active", "intent": "order", "priority": rx.prioridad,
        "code": {"coding": [{"system": SNOMED, "code": "3457005", "display": "Patient referral"}], "text": "Remisión a especialista"},
        "subject": {"reference": pac_ref}, "authoredOn": iso(rx.creado),
        "requester": {"display": settings.interop_nombre},
        "performerType": especialidad_fhir(rx.especialidad),
        "reasonCode": [{"text": rx.motivo}],
        "supportingInfo": [{"reference": f"Observation/{o['id']}"} for o in observaciones] + [{"reference": f"DiagnosticReport/{dr_id}"}, {"reference": f"Consent/con-{cons.id}"}],
    }
    task = {
        "resourceType": "Task", "id": sr_id, "identifier": [{"system": sistema("remision"), "value": str(rx.numero)}],
        "status": "requested", "intent": "order", "priority": rx.prioridad,
        "code": {"coding": [{"system": SNOMED, "code": "3457005", "display": "Patient referral"}]},
        "focus": {"reference": f"ServiceRequest/{sr_id}"}, "basedOn": [{"reference": f"ServiceRequest/{sr_id}"}],
        "for": {"reference": pac_ref}, "authoredOn": iso(rx.creado),
        "requester": {"display": settings.interop_nombre}, "owner": {"display": socio.nombre},
    }
    recursos = [patient, service_request, *observaciones, diagnostic, consent, task]
    return {"resourceType": "Bundle", "type": "transaction", "timestamp": iso(ahora()), "entry": [entrada(x) for x in recursos]}


def _url(socio: Socio, ruta: str) -> str:
    return (socio.url_base or "").rstrip("/") + "/" + ruta.lstrip("/")


async def _cabeceras_auth(cliente: httpx.AsyncClient, socio: Socio) -> dict:
    modo = socio.auth_salida
    secreto = cripto.descifrar(socio.cred_secreto_salida_cif)
    if modo == "ninguno":
        return {}
    if modo == "bearer":
        return {"Authorization": f"Bearer {secreto}"}
    if modo == "api_key":
        valor = f"{socio.cred_id_salida}.{secreto}" if socio.cred_id_salida and "." not in (secreto or "") else secreto
        return {socio.cabecera_api_key or "X-API-Key": valor}
    # oauth2 client_credentials, con el token en memoria hasta 30 s antes de vencer
    guardado = _tokens.get(socio.codigo)
    if guardado and guardado[1] > time.monotonic():
        return {"Authorization": f"Bearer {guardado[0]}"}
    datos = {"grant_type": "client_credentials", "client_id": socio.cred_id_salida or "", "client_secret": secreto or ""}
    if socio.scope_salida:
        datos["scope"] = socio.scope_salida
    r = await cliente.post(_url(socio, socio.ruta_token), data=datos, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise RuntimeError(f"El token de {socio.codigo} respondió {r.status_code}: {r.text[:300]}")
    cuerpo = r.json()
    token, dura = cuerpo["access_token"], int(cuerpo.get("expires_in") or 300)
    _tokens[socio.codigo] = (token, time.monotonic() + max(dura - 30, 10))
    return {"Authorization": f"Bearer {token}"}


async def enviar_fhir(socio: Socio, ruta: str, recurso: dict) -> tuple[int | None, str, dict]:
    """POST al socio. Devuelve (http, cuerpo, cabeceras). http None = no se pudo conectar."""
    cuerpo = json.dumps(recurso, ensure_ascii=False, separators=(",", ":")).encode()
    ts, nonce = str(int(time.time())), uuid.uuid4().hex
    cab = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json, application/json",
           "X-Nonce": nonce, "X-Timestamp": ts}
    if socio.firma_salida:
        cab["X-Signature"] = cripto.firmar(cripto.descifrar(socio.firma_secreto_salida_cif) or "", ts, nonce, "POST", "/" + ruta.lstrip("/"), cuerpo)
    try:
        async with httpx.AsyncClient(timeout=settings.interop_timeout_s, follow_redirects=False) as cliente:
            cab.update(await _cabeceras_auth(cliente, socio))
            r = await cliente.post(_url(socio, ruta), content=cuerpo, headers=cab)
            if r.status_code == 401 and socio.auth_salida == "oauth2":
                _tokens.pop(socio.codigo, None)  # token revocado o vencido antes de tiempo: uno nuevo, una vez
                cab.update(await _cabeceras_auth(cliente, socio))
                cab["X-Nonce"] = uuid.uuid4().hex
                if socio.firma_salida:
                    cab["X-Signature"] = cripto.firmar(cripto.descifrar(socio.firma_secreto_salida_cif) or "", ts, cab["X-Nonce"], "POST", "/" + ruta.lstrip("/"), cuerpo)
                r = await cliente.post(_url(socio, ruta), content=cuerpo, headers=cab)
            return r.status_code, r.text[:4000], dict(r.headers)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:1000], {}


def _id_remoto(cuerpo: str, cabeceras: dict) -> str | None:
    bajas = {k.lower(): v for k, v in cabeceras.items()}
    if bajas.get("x-remision-externa"):
        return bajas["x-remision-externa"][:200]
    try:
        datos = json.loads(cuerpo)
        if isinstance(datos, dict) and datos.get("id"):
            return str(datos["id"])[:200]
    except ValueError:
        pass
    if bajas.get("location"):
        return bajas["location"].rstrip("/").split("/")[-1][:200]
    return None


async def enviar_remision(rx_id: uuid.UUID) -> RemisionExterna:
    """Envía (o reenvía) el Bundle ya armado. Sesión propia: no retiene conexión mientras espera al socio."""
    with SessionLocal() as db:
        rx = db.get(RemisionExterna, rx_id)
        socio = db.get(Socio, rx.socio_id)
        bundle = rx.bundle
        db.expunge(socio)
    codigo, cuerpo, cab = await enviar_fhir(socio, socio.ruta_bundle, bundle)
    with SessionLocal() as db:
        rx = db.get(RemisionExterna, rx_id)
        rx.intentos += 1
        rx.envio_http, rx.envio_detalle, rx.actualizado = codigo, cuerpo, ahora()
        ok = codigo is not None and 200 <= codigo < 300
        rx.estado_envio = "enviada" if ok else "fallida"
        if ok:
            rx.id_remoto = _id_remoto(cuerpo, cab) or rx.id_remoto
        add_audit(db, user_id=rx.medico_id, action="INTEROP_REMISION_ENVIADA" if ok else "INTEROP_ENVIO_FALLIDO",
                  entity_type="INTEROP", entity_id=rx.id,
                  details={"origen": "externo", "institucion": socio.codigo, "numero": rx.numero, "http": codigo,
                           "id_remoto": rx.id_remoto, "intento": rx.intentos, "patient_id": str(rx.patient_id),
                           **({} if ok else {"detalle": (cuerpo or "")[:500]})})
        db.commit()
        db.refresh(rx)
        db.expunge(rx)
    return rx


async def responder_socio(rx_id: uuid.UUID) -> RemisionExterna:
    """Envía al socio el Task con la decisión de nuestro especialista sobre una remisión que nos hizo."""
    with SessionLocal() as db:
        rx = db.get(RemisionExterna, rx_id)
        socio = db.get(Socio, rx.socio_id)
        task = task_de_entrante(rx)
        db.expunge(socio)
    if not socio.url_base:
        codigo, cuerpo = None, "La institución no tiene URL configurada"
    else:
        codigo, cuerpo, _ = await enviar_fhir(socio, socio.ruta_task, task)
    with SessionLocal() as db:
        rx = db.get(RemisionExterna, rx_id)
        ok = codigo is not None and 200 <= codigo < 300
        rx.respuesta_envio, rx.respuesta_http, rx.respuesta_detalle = ("enviada" if ok else "fallida"), codigo, cuerpo
        add_audit(db, user_id=None, action="INTEROP_RESPUESTA_ENVIADA" if ok else "INTEROP_RESPUESTA_FALLIDA",
                  entity_type="INTEROP", entity_id=rx.id,
                  details={"origen": "externo", "institucion": socio.codigo, "numero": rx.numero, "estado": rx.estado,
                           "http": codigo, **({} if ok else {"detalle": (cuerpo or "")[:500]})})
        db.commit()
        db.refresh(rx)
        db.expunge(rx)
    return rx
