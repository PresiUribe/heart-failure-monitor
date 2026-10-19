import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.record_history import RecordHistory


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def model_snapshot(instance) -> dict:
    mapper = inspect(instance).mapper
    return {
        column.key: _json_safe(getattr(instance, column.key))
        for column in mapper.column_attrs
    }


def add_audit(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )
    )


def add_history(
    db: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    previous_data: dict | None,
    new_data: dict | None,
    changed_by: uuid.UUID,
    change_type: str,
) -> None:
    db.add(
        RecordHistory(
            entity_type=entity_type,
            entity_id=entity_id,
            previous_data=previous_data,
            new_data=new_data,
            changed_by=changed_by,
            change_type=change_type,
        )
    )
