import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.record_history import RecordHistory
from app.security.campos import Cifrado, cifrar_campo
import app.security.cadena  # noqa: F401  (registra el sellado de la cadena de auditoría, R28)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def model_snapshot(instance) -> dict:
    """Instantánea para record_history. Los campos cifrados (R27) se guardan cifrados, no en claro."""
    mapper = inspect(instance).mapper
    salida = {}
    for column in mapper.column_attrs:
        valor = getattr(instance, column.key)
        tipo = column.columns[0].type
        if isinstance(tipo, Cifrado):
            valor = cifrar_campo(valor, tipo.contexto)
        salida[column.key] = _json_safe(valor)
    return salida


def add_audit(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    details: dict | None = None,
) -> None:
    # id y fecha se fijan aquí para que el sello de la cadena (R28) se calcule sin releer la base.
    db.add(
        AuditLog(
            id=uuid.uuid4(),
            created_at=datetime.utcnow(),
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
