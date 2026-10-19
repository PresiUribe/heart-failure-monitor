import asyncio, json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.database import SessionLocal, get_db
from app.dependencies.auth import get_current_user, user_from_token
from app.models.ai_report import AIReport
from app.models.alert import Alert
from app.models.notification import Notification
from app.models.referral import Referral
from app.models.user import User
from app.services.event_bus import sse_encode, subscribe, unsubscribe
from app.services.roles import canonical_role

router = APIRouter(tags=["Tiempo real y notificaciones"])

@router.get("/notifications")
def list_notifications(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = list(db.scalars(select(Notification).where(Notification.user_id == current_user.id).order_by(Notification.created_at.desc()).limit(200)))
    return [{"id":str(n.id),"tipo":n.tipo,"mensaje":n.mensaje,"leida":n.leida,"fecha":n.created_at,"paciente_id":str(n.patient_id) if n.patient_id else None, **(n.data or {})} for n in rows]

@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Notificación no encontrada")
    n.leida = True
    db.commit()
    return {"id":str(n.id),"leida":True}

@router.get("/pending")
def pending(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    role = canonical_role(current_user.role.code)
    reports = 0
    refs = 0
    alerts = 0
    if role in {"medico","especialista"}:
        reports = db.scalar(select(func.count()).select_from(AIReport).where(AIReport.status == "PENDING", AIReport.created_by == current_user.id)) or 0
        refs = db.scalar(select(func.count()).select_from(Referral).where(Referral.specialist_id == current_user.id, Referral.status == "PENDING")) or 0
        alerts = db.scalar(select(func.count()).select_from(Alert).where(Alert.assigned_to == current_user.id, Alert.state == "ACTIVE")) or 0
    return {"reportes_por_revisar":reports,"remisiones_recibidas":refs,"alertas_activas":alerts}

@router.get("/events/stream")
async def event_stream(request: Request, token: str = Query(...)):
    with SessionLocal() as db:
        user = user_from_token(db, token)
        uid = str(user.id)
    q = await subscribe(uid)
    async def gen():
        try:
            yield sse_encode({"tipo":"conexion","mensaje":"Canal en tiempo real conectado","fecha":None})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                    yield sse_encode(event)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await unsubscribe(uid, q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@router.websocket("/events/ws")
async def event_websocket(websocket: WebSocket, token: str | None = Query(default=None)):
    """Canal en tiempo real por WebSocket (R17).

    Es el canal principal en producción porque los Quick Tunnels de Cloudflare
    no soportan SSE. Usa el mismo bus de eventos y el mismo alcance por usuario
    que /events/stream. Sin token válido el handshake se rechaza con 403.
    """
    if not token:
        await websocket.close(code=1008)
        return
    try:
        with SessionLocal() as db:
            uid = str(user_from_token(db, token).id)
    except HTTPException:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    q = await subscribe(uid)

    async def drain_client():
        # Detecta el cierre del navegador; los mensajes del cliente se ignoran.
        try:
            while True:
                await websocket.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            pass

    reader = asyncio.create_task(drain_client())
    now = lambda: datetime.now(timezone.utc).isoformat()
    try:
        await websocket.send_text(json.dumps({"tipo": "conexion", "mensaje": "Canal WebSocket conectado", "fecha": now()}))
        while not reader.done():
            getter = asyncio.create_task(q.get())
            done, _ = await asyncio.wait({getter, reader}, timeout=20, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                await websocket.send_text(json.dumps(getter.result(), ensure_ascii=False, default=str))
            else:
                getter.cancel()
                if not reader.done():
                    await websocket.send_text(json.dumps({"tipo": "keepalive", "mensaje": "ping", "fecha": now()}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader.cancel()
        await unsubscribe(uid, q)
