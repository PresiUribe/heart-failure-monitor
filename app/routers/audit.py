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
from app.security.cadena import verificar
from app.services.audit import add_audit

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


@router.get("/verificar")
def verificar_cadena(
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles("ADMIN")),
):
    """R28: recorre la cadena de hashes de toda la auditoría e informa si fue alterada y en qué registro."""
    resultado = verificar(db)
    db.rollback()
    add_audit(db, user_id=admin.id, action="AUDITORIA_VERIFICADA", entity_type="AUDIT", entity_id=None,
              details={"integra": resultado["integra"], "registros": resultado["registros"],
                       "problemas": len(resultado["problemas"])})
    db.commit()
    return resultado


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
