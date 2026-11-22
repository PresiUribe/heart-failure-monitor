"""Endpoints del agente clínico (Semana 13).

POST /agente/chat       médico y especialista: un turno de conversación con el paciente abierto.
POST /agente/decidir    médico: aprobar o rechazar la acción pendiente (proponer_remision).
GET  /agente/conversaciones/{id}  historial del hilo propio (muestra que la memoria sobrevive al reinicio).
GET  /agente/estado     modelo, memoria y límites del harness.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agente import inyeccion
from app.agente.harness import Harness
from app.agente.herramientas import ContextoClinico
from app.agente.modelo import nombre_modelo
from app.config import settings
from app.database import get_db
from app.dependencies.auth import require_roles
from app.models.user import User
from app.rag.seudonimizar import identificadores_paciente, seudonimizar
from app.services.access import ensure_patient_access
from app.interop.seguridad import ip_de
from app.services.audit import add_audit
from app.services.notifications import admin_ids, create_notification, publish_notification
from app.services.identifiers import resolve_patient_identifier
from app.services.pseudonym import patient_pseudonym
from app.services.roles import canonical_role

router = APIRouter(prefix="/agente", tags=["Agente clínico"])
ZONA = timezone(timedelta(hours=-5), "COT")  # Colombia no tiene horario de verano


class MensajeAgente(BaseModel):
    mensaje: str = Field(min_length=2, max_length=1000)
    conversacion_id: str = Field(default="principal", pattern=r"^[\w-]{1,40}$")
    paciente_id: str | None = Field(default=None, description="UUID, documento o código del paciente abierto")


class DecisionAgente(BaseModel):
    conversacion_id: str = Field(pattern=r"^[\w-]{1,40}$")
    decision: Literal["approve", "reject"]
    motivo: str | None = Field(default=None, max_length=300)


def _harness(request: Request) -> Harness:
    harness = getattr(request.app.state, "agente", None)
    if harness is None:
        raise HTTPException(status_code=503, detail="El agente aún se está iniciando")
    return harness


@router.post("/chat")
async def chat(body: MensajeAgente, request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(require_roles("MEDICO", "ESPECIALISTA"))):    # R03: contable -> 403
    harness = _harness(request)
    ip = ip_de(request)
    rol = canonical_role(current_user.role.code)
    # R25: bloqueo temporal vigente (por usuario o por IP) tras varios intentos de manipulación
    fin = await asyncio.to_thread(inyeccion.bloqueo_vigente, current_user.id, ip)
    if fin is not None:
        raise HTTPException(status_code=429, headers={"Retry-After": str(max(int((fin - datetime.now(timezone.utc)).total_seconds()), 1))},
                            detail=f"Acceso al asistente bloqueado temporalmente por intentos de manipulación. Intente después de las "
                                   f"{fin.astimezone(ZONA).strftime('%H:%M')}. El administrador fue notificado.")
    patient, alias, mensaje = None, None, seudonimizar(body.mensaje, [], "")
    if body.paciente_id:
        patient = resolve_patient_identifier(db, body.paciente_id)
        ensure_patient_access(db, user=current_user, patient_id=patient.id)              # 403 si no está asignado
        alias = patient_pseudonym(str(patient.id))                                        # R12
        mensaje = seudonimizar(body.mensaje, identificadores_paciente(patient), alias)  # el nombre no sale
    patrones = inyeccion.detectar(body.mensaje)
    if patrones:
        return await _rechazar_inyeccion(db, current_user, rol, ip, patrones, mensaje, body.conversacion_id, alias)
    ctx = ContextoClinico(usuario=current_user.username, usuario_id=str(current_user.id),
                          rol=rol, conversacion=body.conversacion_id,
                          paciente_id=str(patient.id) if patient else None, paciente_alias=alias, ip=ip)
    user_id, patient_uuid = current_user.id, (patient.id if patient else None)
    # Libera la conexión a la base mientras el agente piensa: con 10 médicos a la vez, retenerla agota el pool.
    db.rollback()
    inicio = time.perf_counter()
    resultado = await harness.atender(ctx, mensaje)
    add_audit(db, user_id=user_id, action="AGENTE_CHAT", entity_type="PATIENT" if patient_uuid else "AGENTE",
              entity_id=patient_uuid,
              details={"conversacion": resultado["conversacion"], "estado": resultado["estado"], "seudonimo": alias,
                       "mensaje": mensaje, "herramientas": resultado.get("herramientas", []),
                       "duracion_ms": int((time.perf_counter() - inicio) * 1000)})               # R13
    db.commit()
    return {**resultado, "seudonimo": alias}


async def _rechazar_inyeccion(db: Session, user: User, rol: str, ip: str, patrones: list[str], mensaje: str,
                              conversacion: str, alias: str | None) -> dict:
    """R25: el mensaje no llega al modelo. Se registra quién, desde dónde y qué intentó; se avisa al admin."""
    await asyncio.to_thread(inyeccion.registrar, usuario_id=user.id, usuario=user.username, rol=rol, ip=ip,
                            origen="pregunta", patrones=patrones, extracto=mensaje)
    fin = await asyncio.to_thread(inyeccion.bloqueo_vigente, user.id, ip)
    add_audit(db, user_id=user.id, action="AGENTE_INYECCION", entity_type="AGENTE", entity_id=None,
              details={"usuario": user.username, "rol": rol, "ip": ip, "patrones": patrones, "mensaje": mensaje[:300],
                       "conversacion": conversacion, "paciente": alias, "bloqueado_hasta": fin.isoformat() if fin else None})
    avisos = [create_notification(db, user_id=aid, tipo="agente_inyeccion",
                                  mensaje=f"Intento de manipulación del asistente por {user.username} ({rol}) desde {ip}: "
                                          f"{', '.join(patrones)}" + (". Usuario bloqueado temporalmente." if fin else ""),
                                  data={"usuario": user.username, "ip": ip, "patrones": patrones, "bloqueado": bool(fin)})
              for aid in admin_ids(db)]
    db.commit()
    for n in avisos:
        db.refresh(n)
        await publish_notification(n)
    return {"estado": "rechazado_seguridad", "conversacion": Harness.hilo(user.username, conversacion), "fuentes": [],
            "respuesta": ("No puedo seguir instrucciones que cambien mis reglas, revelen mi configuración o amplíen el acceso a "
                          "datos. Solo trabajo con el paciente abierto y con sus permisos. El intento quedó registrado."),
            "patrones": patrones, "bloqueado_hasta": fin.isoformat() if fin else None}


@router.get("/incidentes")
def incidentes(db: Session = Depends(get_db), _: User = Depends(require_roles("ADMIN"))):
    """R25: intentos de manipulación (usuario, rol, IP, patrón y extracto seudonimizado), del más reciente al más antiguo."""
    filas = db.execute(text("SELECT id, usuario, rol, ip, origen, patrones, extracto, liberado, creado FROM agente_incidentes "
                            "ORDER BY creado DESC LIMIT 200")).mappings().all()
    return [dict(f) for f in filas]


class Desbloqueo(BaseModel):
    usuario: str | None = None
    ip: str | None = None


@router.post("/desbloquear")
async def desbloquear(body: Desbloqueo, db: Session = Depends(get_db), admin: User = Depends(require_roles("ADMIN"))):
    if not (body.usuario or body.ip):
        raise HTTPException(status_code=422, detail="Indique usuario o ip")
    n = await asyncio.to_thread(inyeccion.liberar, body.usuario, body.ip)
    add_audit(db, user_id=admin.id, action="AGENTE_DESBLOQUEO", entity_type="AGENTE", entity_id=None,
              details={"usuario": body.usuario, "ip": body.ip, "incidentes_liberados": n})
    db.commit()
    return {"liberados": n}


@router.post("/decidir")
async def decidir(body: DecisionAgente, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(require_roles("MEDICO", "ESPECIALISTA"))):
    harness = _harness(request)
    usuario, rol, user_id = current_user.username, canonical_role(current_user.role.code), current_user.id
    db.rollback()   # no retener la conexión mientras se reanuda el agente
    resultado = await harness.decidir(usuario, rol, body.conversacion_id, body.decision, body.motivo)
    if resultado["estado"] == "no_autorizado":
        raise HTTPException(status_code=403, detail="Solo el médico aprueba o rechaza remisiones (R08)")
    add_audit(db, user_id=user_id, action=f"AGENTE_{body.decision.upper()}", entity_type="AGENTE", entity_id=None,
              details={"conversacion": resultado["conversacion"], "estado": resultado["estado"], "motivo": body.motivo,
                       "herramientas": resultado.get("herramientas", [])})
    db.commit()
    return resultado


@router.get("/conversaciones/{conversacion_id}")
async def conversacion(conversacion_id: str, request: Request,
                       current_user: User = Depends(require_roles("MEDICO", "ESPECIALISTA"))):
    if not conversacion_id.replace("-", "").replace("_", "").isalnum() or len(conversacion_id) > 40:
        raise HTTPException(status_code=422, detail="conversacion_id inválido")
    harness = _harness(request)
    # Solo el hilo propio: el prefijo usuario: sale del token.
    return {"conversacion": harness.hilo(current_user.username, conversacion_id), "memoria": harness.memoria,
            "mensajes": await harness.historial(current_user.username, conversacion_id),
            "pendiente": await harness.pendiente(current_user.username, conversacion_id)}


@router.delete("/preferencias")
async def borrar_preferencias(request: Request, db: Session = Depends(get_db),
                              current_user: User = Depends(require_roles("MEDICO", "ESPECIALISTA"))):
    """Borra las preferencias que el agente guardó del usuario (memoria de largo plazo)."""
    user_id, usuario = current_user.id, current_user.username
    db.rollback()
    n = await _harness(request).borrar_preferencias(usuario)
    add_audit(db, user_id=user_id, action="AGENTE_PREFERENCIAS_BORRADAS", entity_type="AGENTE", entity_id=None,
              details={"borradas": n})
    db.commit()
    return {"borradas": n}


@router.get("/estado")
def estado(request: Request, current_user: User = Depends(require_roles("ADMIN", "MEDICO", "ESPECIALISTA"))):
    harness = _harness(request)
    return {"modelo": nombre_modelo(), "memoria": harness.memoria,
            "limites": {"max_llamadas_modelo_por_turno": settings.agente_max_llamadas_modelo,
                        "timeout_s": settings.agente_timeout_s,
                        "max_llamadas_simultaneas_al_proveedor": settings.agente_max_simultaneas,
                        "ventana_mensajes": settings.agente_ventana_mensajes}}
