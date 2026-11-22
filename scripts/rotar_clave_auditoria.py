"""Rota la llave de la cadena de auditoría (R28) sin perder la auditoría.

Comprueba que la cadena actual es íntegra con la llave ANTERIOR (o con la nueva, para los registros que
ya se sellaron con ella) y solo entonces vuelve a sellar todo con AUDITORIA_CLAVE. Si encuentra un registro
que no cuadra con ninguna de las dos llaves, se detiene sin tocar nada: eso es manipulación, no rotación.
Deja constancia en la propia cadena (AUDITORIA_LLAVE_ROTADA).

    1. Ponga la nueva AUDITORIA_CLAVE en .env y recree la API:  docker compose up -d
    2. docker compose exec api python scripts/rotar_clave_auditoria.py
       (si la anterior no era la derivada:  -e AUDITORIA_CLAVE_ANTERIOR=la_vieja)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402
from app.security.cadena import CANDADO, GENESIS, calcular, contenido, llave_de  # noqa: E402


def main() -> int:
    vieja = llave_de(os.environ.get("AUDITORIA_CLAVE_ANTERIOR") or None)
    nueva = llave_de(settings.auditoria_clave)
    if hmac.compare_digest(vieja, nueva):
        print("La llave anterior y la actual son la misma: nada que rotar.")
        return 0
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CANDADO})
        filas = conn.execute(text("SELECT seq, id, user_id, action, entity_type, entity_id, details, created_at, prev_hash, hash "
                                  "FROM audit_log WHERE seq IS NOT NULL ORDER BY seq")).all()
        prev_esperado, seq_esperado = GENESIS, 1
        for f in filas:
            cuerpo = contenido(f.seq, f.id, f.user_id, f.action, f.entity_type, f.entity_id, f.details, f.created_at)
            if f.seq != seq_esperado or f.prev_hash != prev_esperado or not (
                    hmac.compare_digest(calcular(f.prev_hash, cuerpo, vieja), f.hash or "")
                    or hmac.compare_digest(calcular(f.prev_hash, cuerpo, nueva), f.hash or "")):
                print(f"El registro {f.seq} ({f.action}) no cuadra con ninguna de las dos llaves: la cadena fue alterada.")
                print("No se rotó nada. Revise GET /audit/verificar.")
                return 1
            prev_esperado, seq_esperado = f.hash, f.seq + 1

        conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_solo_agregar"))
        prev = GENESIS
        for f in filas:
            h = calcular(prev, contenido(f.seq, f.id, f.user_id, f.action, f.entity_type, f.entity_id, f.details, f.created_at), nueva)
            conn.execute(text("UPDATE audit_log SET prev_hash=:p, hash=:h WHERE seq=:s"), {"p": prev, "h": h, "s": f.seq})
            prev = h
        conn.execute(text("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_solo_agregar"))

        seq, id_, ahora = len(filas) + 1, uuid.uuid4(), datetime.utcnow()
        detalles = {"registros_resellados": len(filas),
                    "huella_llave_nueva": hashlib.sha256(nueva).hexdigest()[:8], "origen": "script"}
        h = calcular(prev, contenido(seq, id_, None, "AUDITORIA_LLAVE_ROTADA", "AUDIT", None, detalles, ahora), nueva)
        conn.execute(text("INSERT INTO audit_log (id, user_id, action, entity_type, entity_id, details, created_at, seq, prev_hash, hash) "
                          "VALUES (:i, NULL, 'AUDITORIA_LLAVE_ROTADA', 'AUDIT', NULL, CAST(:d AS JSONB), :c, :s, :p, :h)"),
                     {"i": id_, "d": json.dumps(detalles), "c": ahora, "s": seq, "p": prev, "h": h})
    print(f"Llave de auditoría rotada: {len(filas)} registros verificados y vueltos a sellar. Verifique con GET /audit/verificar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
