import asyncio
import contextlib

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from app.routers import audit, auth, encounters, fhir, observations, patients, profile, setup, users
from app.routers import agente, ai, billing, fhir_events, fhir_gateway, history, imaging, me, notifications, rag, referrals
from app.routers import interop, interop_clinico
from app.interop.esquema import asegurar_esquema_interop
from app.interop.fhir import InteropError, operation_outcome, respuesta_fhir
from app.interop.servicio import auditar_rechazo
from app.agente.memoria import iniciar_agente
from app.rag.indice import iniciar_rag
from app.services.alert_escalation import alert_escalation_loop
from app.services.fhir_subscription import ensure_observation_subscription

app = FastAPI(
    title="Heart Failure Rural Monitoring API",
    description="Sistema activo de Salud Digital para seguimiento de insuficiencia cardiaca: PostgreSQL + HAPI FHIR R4 + PACS + IA + MongoDB + SSE.",
    version="2.0.0",
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_per_minute)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(InteropError)
async def interop_error_handler(request: Request, exc: InteropError):
    """Todo rechazo a un socio es un OperationOutcome (R30) y queda auditado con origen externo."""
    if exc.auditar:
        await auditar_rechazo(request, exc)
    return respuesta_fhir(operation_outcome(exc.issues), exc.status, exc.cabeceras or None)

for router in [
    setup.router,
    auth.router,
    me.router,
    users.router,
    patients.router,
    profile.router,
    encounters.router,
    observations.router,
    audit.router,
    fhir.router,
    imaging.router,
    ai.router,
    referrals.router,
    billing.router,
    notifications.router,
    history.router,
    fhir_events.router,
    fhir_gateway.router,
    rag.router,
    agente.router,
    interop.router,
    interop_clinico.router,
]:
    app.include_router(router)


@app.on_event("startup")
async def startup_background_services():
    # Interoperabilidad (proyecto final): tablas nuevas antes de cualquier consulta del ORM.
    await asyncio.to_thread(asegurar_esquema_interop)
    # Start alert escalation immediately. It stays in-process because the academic
    # deployment uses one API replica.
    app.state.alert_escalation_task = asyncio.create_task(alert_escalation_loop())
    # HAPI takes longer to boot than FastAPI; configure its REST-hook in background.
    app.state.fhir_subscription_task = asyncio.create_task(ensure_observation_subscription())
    # RAG: crea la tabla pgvector y carga las guías cuando el servicio de embeddings responda.
    app.state.rag_task = asyncio.create_task(iniciar_rag())
    # Agente (Semana 13): harness con memoria en PostgreSQL; la pila cierra el pool al apagar.
    app.state.agente_pila = contextlib.AsyncExitStack()
    app.state.agente = await iniciar_agente(app.state.agente_pila)


@app.on_event("shutdown")
async def shutdown_background_services():
    for name in ("alert_escalation_task", "fhir_subscription_task", "rag_task"):
        task = getattr(app.state, name, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    pila = getattr(app.state, "agente_pila", None)
    if pila:
        await pila.aclose()


@app.get("/", tags=["Sistema"])
def root():
    return {
        "message": "Heart Failure Rural Monitoring API",
        "version": "2.0.0",
        "docs": "/docs",
        "modules": ["FHIR", "PACS", "ML", "MongoDB", "SSE"],
    }


@app.get("/health", tags=["Sistema"])
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/health/db", tags=["Sistema"])
def database_health(db: Session = Depends(get_db)):
    database_name = db.execute(text("SELECT current_database()" )).scalar_one()
    return {
        "status": "ok",
        "database": "connected",
        "provider": settings.database_provider,
        "database_name": database_name,
    }
