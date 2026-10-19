import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.alert import Alert
from app.services.audit import add_audit
from app.services.notifications import admin_ids, create_notification, publish_notification

logger = logging.getLogger(__name__)


async def escalate_due_alerts_once() -> int:
    cutoff = datetime.utcnow() - timedelta(seconds=settings.alert_escalation_seconds)
    pending_to_publish = []
    escalated_count = 0

    with SessionLocal() as db:
        alerts = list(
            db.scalars(
                select(Alert).where(
                    Alert.state == "ACTIVE",
                    Alert.escalated.is_(False),
                    Alert.created_at <= cutoff,
                )
            )
        )
        admins = admin_ids(db)
        for alert in alerts:
            alert.escalated = True
            escalated_count += 1
            add_audit(
                db,
                user_id=None,
                action="CRITICAL_ALERT_ESCALATED",
                entity_type="ALERT",
                entity_id=alert.id,
                details={
                    "patient_id": str(alert.patient_id),
                    "assigned_to": str(alert.assigned_to),
                    "after_seconds": settings.alert_escalation_seconds,
                },
            )
            for admin_id in admins:
                pending_to_publish.append(
                    create_notification(
                        db,
                        user_id=admin_id,
                        tipo="alerta_escalada",
                        mensaje="Alerta crítica sin reconocer dentro del tiempo definido.",
                        patient_id=alert.patient_id,
                        data={"alerta_id": str(alert.id), "clinico_id": str(alert.assigned_to)},
                    )
                )

        if alerts:
            db.commit()
            for n in pending_to_publish:
                db.refresh(n)

    for notification in pending_to_publish:
        await publish_notification(notification)

    return escalated_count


async def alert_escalation_loop() -> None:
    interval = max(5, min(30, settings.alert_escalation_seconds // 4 or 5))
    while True:
        try:
            await escalate_due_alerts_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error while escalating critical alerts")
        await asyncio.sleep(interval)
