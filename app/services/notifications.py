import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.notification import Notification
from app.models.role import Role
from app.models.user import User
from app.services.event_bus import publish
from app.services.roles import canonical_role


def create_notification(db: Session, *, user_id: uuid.UUID, tipo: str, mensaje: str, patient_id=None, data=None) -> Notification:
    n = Notification(user_id=user_id, tipo=tipo, mensaje=mensaje, patient_id=patient_id, data=data or {})
    db.add(n)
    db.flush()
    return n


async def publish_notification(notification: Notification) -> None:
    event = {
        "tipo": notification.tipo,
        "mensaje": notification.mensaje,
        "fecha": notification.created_at,
    }
    if notification.patient_id:
        event["paciente_id"] = str(notification.patient_id)
    if notification.data:
        event.update(notification.data)
    await publish([str(notification.user_id)], event)


def admin_ids(db: Session) -> list[uuid.UUID]:
    rows = db.execute(select(User.id, Role.code).join(Role, Role.id == User.role_id).where(User.is_active.is_(True))).all()
    return [uid for uid, code in rows if canonical_role(code) == "admin"]


def role_user_ids(db: Session, canonical: str) -> list[uuid.UUID]:
    rows = db.execute(
        select(User.id, Role.code)
        .join(Role, Role.id == User.role_id)
        .where(User.is_active.is_(True), User.deleted_at.is_(None))
    ).all()
    return [uid for uid, code in rows if canonical_role(code) == canonical]
