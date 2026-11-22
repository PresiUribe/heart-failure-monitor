"""R28 · Auditoría a prueba de manipulación: cadena de hashes sobre audit_log.

Cada registro guarda prev_hash (el hash del anterior) y hash = HMAC-SHA256(AUDITORIA_CLAVE, prev_hash + contenido).
- El orden de la cadena (seq) es el orden de confirmación: se sella en el evento before_commit de la sesión,
  bajo un candado de PostgreSQL de corta duración. Si la transacción se deshace, no queda nada sellado.
- Con llave (HMAC) y no un SHA-256 simple: quien edite la base no puede recalcular la cadena sin la llave,
  que vive solo en el entorno.
- Un disparador impide UPDATE/DELETE de registros sellados (capa 1). Si alguien con permisos de dueño lo
  desactiva y altera un registro, GET /audit/verificar lo detecta (capa 2).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.audit_log import AuditLog

CANDADO = 724306
GENESIS = "0" * 64


def llave_de(clave: str | None) -> bytes:
    """Llave HMAC a partir del valor de AUDITORIA_CLAVE (vacío = derivada de JWT_SECRET_KEY)."""
    return hashlib.sha256((clave or f"auditoria-derivada:{settings.jwt_secret_key}").encode()).digest()


def _llave() -> bytes:
    return llave_de(settings.auditoria_clave)


def _fecha(valor) -> str | None:
    return valor.isoformat() if isinstance(valor, datetime) else (str(valor) if valor is not None else None)


def contenido(seq, id_, user_id, action, entity_type, entity_id, details, created_at) -> str:
    return json.dumps({"seq": int(seq), "id": str(id_), "user_id": str(user_id) if user_id else None, "action": action,
                       "entity_type": entity_type, "entity_id": str(entity_id) if entity_id else None,
                       "details": details, "created_at": _fecha(created_at)},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def calcular(prev_hash: str, cuerpo: str, llave: bytes | None = None) -> str:
    return hmac.new(llave or _llave(), (prev_hash + "|" + cuerpo).encode(), hashlib.sha256).hexdigest()


def _ultimo(conn_o_sesion) -> tuple[int, str]:
    fila = conn_o_sesion.execute(text("SELECT seq, hash FROM audit_log WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1")).first()
    return (int(fila[0]), fila[1]) if fila else (0, GENESIS)


@event.listens_for(Session, "before_flush")
def _anotar(session: Session, _ctx, _instancias) -> None:
    nuevos = [o for o in session.new if isinstance(o, AuditLog)]
    if nuevos:
        session.info.setdefault("auditoria_por_sellar", []).extend(nuevos)


@event.listens_for(Session, "before_commit")
def _sellar(session: Session) -> None:
    pendientes = [o for o in session.new if isinstance(o, AuditLog)]
    if not pendientes and not session.info.get("auditoria_por_sellar"):
        return
    session.flush()  # ids y fechas definitivos; también dispara _anotar para los que faltaban
    por_sellar = [o for o in session.info.pop("auditoria_por_sellar", []) if o.hash is None]
    if not por_sellar:
        return
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CANDADO})  # se libera al confirmar
    seq, prev = _ultimo(session)
    for o in por_sellar:
        seq += 1
        o.seq, o.prev_hash = seq, prev
        o.hash = calcular(prev, contenido(seq, o.id, o.user_id, o.action, o.entity_type, o.entity_id, o.details, o.created_at))
        prev = o.hash


@event.listens_for(Session, "after_soft_rollback")
def _olvidar(session: Session, _previa) -> None:
    session.info.pop("auditoria_por_sellar", None)


def sellar_existentes(conn) -> int:
    """Sella los registros previos a la cadena (o insertados por fuera del ORM), en orden de creación."""
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CANDADO})
    seq, prev = _ultimo(conn)
    filas = conn.execute(text("SELECT id, user_id, action, entity_type, entity_id, details, created_at FROM audit_log "
                              "WHERE seq IS NULL ORDER BY created_at, id")).all()
    for f in filas:
        seq += 1
        h = calcular(prev, contenido(seq, f.id, f.user_id, f.action, f.entity_type, f.entity_id, f.details, f.created_at))
        conn.execute(text("UPDATE audit_log SET seq=:s, prev_hash=:p, hash=:h WHERE id=:i"), {"s": seq, "p": prev, "h": h, "i": f.id})
        prev = h
    return len(filas)


def verificar(db: Session, llave: bytes | None = None) -> dict:
    """Recorre toda la cadena y dice si fue alterada, dónde y cómo."""
    esperado_prev, esperado_seq, total = GENESIS, 1, 0
    problemas: list[dict] = []
    res = db.execute(text("SELECT seq, id, user_id, action, entity_type, entity_id, details, created_at, prev_hash, hash "
                          "FROM audit_log WHERE seq IS NOT NULL ORDER BY seq")).yield_per(2000)
    for f in res:
        total += 1
        if f.seq != esperado_seq:
            problemas.append({"seq": esperado_seq, "tipo": "faltan_registros",
                              "detalle": f"Se esperaba el registro {esperado_seq} y sigue el {f.seq}: se borraron {f.seq - esperado_seq}"})
        if f.prev_hash != esperado_prev:
            problemas.append({"seq": f.seq, "tipo": "enlace_roto", "detalle": "prev_hash no coincide con el hash del registro anterior"})
        calculado = calcular(f.prev_hash or "", contenido(f.seq, f.id, f.user_id, f.action, f.entity_type, f.entity_id, f.details, f.created_at), llave)
        if not hmac.compare_digest(calculado, f.hash or ""):
            problemas.append({"seq": f.seq, "id": str(f.id), "tipo": "contenido_alterado",
                              "detalle": f"El registro {f.seq} ({f.action}) fue modificado después de sellarse"})
        esperado_prev, esperado_seq = f.hash, f.seq + 1
        del problemas[50:]  # se cuentan todos los registros, pero se reportan como máximo 50 problemas
    sin_sellar = db.execute(text("SELECT count(*) FROM audit_log WHERE seq IS NULL AND created_at < now() - interval '1 minute'")).scalar()
    if sin_sellar:
        problemas.append({"tipo": "sin_sellar", "detalle": f"{sin_sellar} registros fuera de la cadena (insertados por fuera de la aplicación)"})
    return {"integra": not problemas, "registros": total, "ultimo_hash": esperado_prev if total else None,
            "problemas": problemas, "primer_problema": problemas[0] if problemas else None}
