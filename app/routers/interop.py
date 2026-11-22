"""Rutas de entrada para las instituciones socias (contrato común con los demás equipos).

Públicas (detrás de nginx en /api): /interop/metadata, /oauth/token, /interop/Bundle, /interop/Task, /interop/Task/{id}.
Las credenciales de sistema SOLO abren estas rutas; un token de usuario no sirve aquí y viceversa (R31).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.interop import cripto
from app.interop.fhir import (InteropError, numero_desde, respuesta_fhir, sistema, task_de_entrante,
                              task_de_saliente, validar_bundle)
from app.interop.seguridad import (SCOPE_BUNDLE, SCOPE_TASK, autenticar, credencial_basic, ip_de, normalizar_scopes)
from app.interop.servicio import auditar, procesar_bundle, procesar_task
from app.models.interop import RemisionExterna, Socio
from app.services.audit import add_audit
from app.services.notifications import publish_notification

router = APIRouter(tags=["Interoperabilidad (socios)"])


def _json(cuerpo: bytes):
    try:
        return json.loads(cuerpo or b"null")
    except (ValueError, UnicodeDecodeError):
        raise InteropError(400, "El cuerpo no es JSON válido", code="structure")


@router.get("/interop/metadata")
def metadata():
    """Qué aceptamos y cómo autenticarse. Público y sin datos de pacientes."""
    return respuesta_fhir({
        "resourceType": "CapabilityStatement", "status": "active", "kind": "instance", "fhirVersion": "4.0.1",
        "date": "2026-11-22", "publisher": settings.interop_nombre, "format": ["application/fhir+json", "application/json"],
        "implementation": {"description": f"{settings.interop_nombre} · entrada de remisiones entre instituciones"},
        "rest": [{
            "mode": "server",
            "documentation": ("Credencial de sistema por institución: OAuth2 client_credentials (POST /oauth/token, formulario o "
                              "HTTP Basic; token de 10 min) o X-API-Key: <client_id>.<client_secret>. Cabecera x-nonce "
                              "recomendada (16-128 [A-Za-z0-9_-], repetida = 409). Firma HMAC opcional por institución: "
                              "X-Timestamp, X-Nonce, X-Signature = v1=hex(HMAC-SHA256(secreto, 'v1\\n<ts>\\n<nonce>\\nPOST\\n<ruta>\\n<sha256 cuerpo>')). "
                              "Límite 1 MB. Errores: OperationOutcome con un issue por problema."),
            "security": {
                "service": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/restful-security-service", "code": "OAuth"}]}],
                "extension": [{"url": "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris",
                               "extension": [{"url": "token", "valueUri": "/oauth/token"}]}],
                "description": "Scopes: system/ServiceRequest.write (alias bundle:write) y system/Task.write (alias task:write)."},
            "resource": [
                {"type": "Bundle", "interaction": [{"code": "create"}],
                 "documentation": "POST /interop/Bundle: 1 Patient, 1 ServiceRequest (SNOMED, motivo, prioridad, id o identifier), "
                                  "0..n Observation (LOINC, UCUM), 0..1 DiagnosticReport aprobado (final), 0..1 Consent, 0..1 Task. 201 Task."},
                {"type": "Task", "interaction": [{"code": "create"}, {"code": "read"}],
                 "documentation": "POST /interop/Task: respuesta (accepted/rejected...) a una remisión nuestra; focus/basedOn a "
                                  "ServiceRequest/rem-<n> o id rem-<n>. GET /interop/Task/{id}: estado de una remisión que nos enviaron."},
            ],
            "extension": [{"url": f"{settings.interop_sistema_base}/StructureDefinition/identificadores",
                           "extension": [{"url": "remision", "valueUri": sistema("remision")},
                                         {"url": "paciente-federado", "valueUri": sistema("paciente-federado")}]}],
        }],
    })


def _error_oauth(status: int, error: str, descripcion: str) -> JSONResponse:
    cab = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if error == "invalid_client":
        cab["WWW-Authenticate"] = 'Basic realm="interop"'
    return JSONResponse({"error": error, "error_description": descripcion}, status_code=status, headers=cab)


def _auditar_token(accion: str, request: Request, socio: Socio | None, client_id: str | None, **extra) -> None:
    with SessionLocal() as s:
        add_audit(s, user_id=None, action=accion, entity_type="INTEROP", entity_id=None,
                  details={"origen": "externo", "ip": ip_de(request), "client_id": client_id,
                           "institucion": socio.codigo if socio else None, **extra})
        s.commit()


@router.post("/oauth/token")
async def token(request: Request):
    """OAuth2 client_credentials (RFC 6749 §4.4). invalid_client es idéntico para cliente inexistente, inactivo o secreto malo."""
    tipo = request.headers.get("content-type", "")
    if "json" in tipo:
        try:
            datos = json.loads(await request.body() or b"{}")
        except ValueError:
            return _error_oauth(400, "invalid_request", "Cuerpo inválido")
    else:
        datos = dict(await request.form())
    cid_b, sec_b = credencial_basic(request)
    client_id = cid_b or datos.get("client_id")
    secreto = sec_b or datos.get("client_secret")
    if datos.get("grant_type") != "client_credentials":
        return _error_oauth(400, "unsupported_grant_type", "Solo se admite grant_type=client_credentials")
    with SessionLocal() as s:
        socio = s.scalar(select(Socio).where(Socio.client_id == (client_id or "")))
        valido = cripto.verificar_secreto(secreto or "", socio.secreto_hash if socio else None)
        if not valido or socio is None or socio.estado != "activo":
            s.expunge_all()
            _auditar_token("INTEROP_TOKEN_RECHAZADO", request, socio, client_id, motivo="invalid_client")
            return _error_oauth(401, "invalid_client", "Credenciales de cliente inválidas")
        asignados = normalizar_scopes(socio.scopes)
        pedidos = normalizar_scopes(datos.get("scope"))
        if pedidos and any(p not in asignados for p in pedidos):
            _auditar_token("INTEROP_TOKEN_RECHAZADO", request, socio, client_id, motivo="invalid_scope", pedidos=pedidos)
            return _error_oauth(400, "invalid_scope", f"Scopes permitidos: {' '.join(asignados)}")
        concedidos = pedidos or asignados
        acceso, dura = cripto.emitir_token(socio.client_id, concedidos)
        _auditar_token("INTEROP_TOKEN_EMITIDO", request, socio, client_id, scope=" ".join(concedidos))
    return JSONResponse({"access_token": acceso, "token_type": "Bearer", "expires_in": dura, "scope": " ".join(concedidos)},
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.post("/interop/Bundle")
async def recibir_bundle(request: Request, db: Session = Depends(get_db)):
    """Nos remiten un paciente. Válido → 201 Task requested; inválido → 422 y no se guarda nada."""
    ent = await autenticar(request, db, scope=SCOPE_BUNDLE, ruta="/interop/Bundle")
    rem = validar_bundle(_json(ent.cuerpo))
    try:
        rx, aviso = procesar_bundle(db, ent.socio, rem, ip_de(request))
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(rx)
    db.refresh(aviso)
    await publish_notification(aviso)
    task = task_de_entrante(rx, ent.socio.nombre)
    return respuesta_fhir(task, 201, {"Location": f"/interop/Task/rem-{rx.numero}", "X-Remision-Externa": f"rem-{rx.numero}"})


@router.post("/interop/Task")
async def recibir_task(request: Request, db: Session = Depends(get_db)):
    """El socio responde (acepta/rechaza) una remisión que le enviamos. Reintentos idénticos: 200 sin nuevo aviso."""
    ent = await autenticar(request, db, scope=SCOPE_TASK, ruta="/interop/Task")
    try:
        rx, aviso = procesar_task(db, ent.socio, _json(ent.cuerpo), ip_de(request))
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(rx)
    if aviso is not None:
        db.refresh(aviso)
        await publish_notification(aviso)
    return respuesta_fhir(task_de_saliente(rx))


@router.get("/interop/Task/{task_id}")
async def consultar_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Estado de una remisión que ESTA institución nos envió (solo ve las suyas)."""
    ent = await autenticar(request, db, scope=SCOPE_BUNDLE, ruta=f"/interop/Task/{task_id}")
    n = numero_desde(task_id)
    q = select(RemisionExterna).where(RemisionExterna.socio_id == ent.socio.id, RemisionExterna.direccion == "entrante")
    rx = db.scalar(q.where(RemisionExterna.numero == n)) if n is not None else None
    if rx is None:
        rx = db.scalar(q.where(RemisionExterna.task_id_externo == task_id))
    if rx is None:
        raise InteropError(404, f"No existe la remisión {task_id} para su institución", code="not-found")
    auditar(db, "INTEROP_TASK_CONSULTADO", socio=ent.socio, entity_id=rx.id, ip=ip_de(request), numero=rx.numero)
    db.commit()
    return respuesta_fhir(task_de_entrante(rx, ent.socio.nombre))
