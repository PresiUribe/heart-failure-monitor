import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import require_roles
from app.models.audit_log import AuditLog
from app.models.record_history import RecordHistory
from app.models.user import User
from app.schemas.audit import AuditLogResponse, RecordHistoryResponse

router = APIRouter(prefix="/audit", tags=["Auditoría"])


@router.get(
    "/logs",
    response_model=list[AuditLogResponse],
)
def list_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ADMIN")),
):
    return list(
        db.scalars(
            select(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    )


@router.get(
    "/history/{entity_type}/{entity_id}",
    response_model=list[RecordHistoryResponse],
)
def get_entity_history(
    entity_type: str,
    entity_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ADMIN")),
):
    return list(
        db.scalars(
            select(RecordHistory)
            .where(
                RecordHistory.entity_type == entity_type.upper(),
                RecordHistory.entity_id == entity_id,
            )
            .order_by(RecordHistory.changed_at.desc())
        )
    )
