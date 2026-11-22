"""Migraciones del proyecto final, aplicadas al arrancar (idempotentes), como hacen el RAG y el agente.

Así una base existente del Corte 2 queda al día con solo `docker compose up -d --build`:
- 005 interoperabilidad (R29–R33).
- 006 seguridad: R27 cifra documento/teléfono/dirección/vereda existentes y R28 sella la auditoría previa.
"""
import logging
from pathlib import Path

from sqlalchemy import text

from app.database import engine

log = logging.getLogger(__name__)
CARPETA = Path(__file__).resolve().parents[2] / "sql" / "migrations"
MIGRACIONES = ["005_interoperabilidad.sql", "006_seguridad.sql"]
CAMPOS_PACIENTE = ("document_number", "phone", "address", "rural_area")
SENSIBLES_EN_DETALLE = ("document_number", "phone", "address", "rural_area", "documento", "telefono", "direccion")


def _cifrar_existentes(conn) -> int:
    from app.security.campos import PREFIJO, cifrar_campo, indice_ciego

    n = 0
    filas = conn.execute(text("SELECT id, document_number, phone, address, rural_area, document_hash FROM patients")).mappings().all()
    for f in filas:
        cambios = {}
        for campo in CAMPOS_PACIENTE:
            v = f[campo]
            if v is not None and not str(v).startswith(PREFIJO):
                cambios[campo] = cifrar_campo(v, f"patients.{campo}")
        if f["document_hash"] is None:
            from app.security.campos import descifrar_campo
            cambios["document_hash"] = indice_ciego(descifrar_campo(f["document_number"], "patients.document_number"))
        if cambios:
            sets = ", ".join(f"{k} = :{k}" for k in cambios)
            conn.execute(text(f"UPDATE patients SET {sets} WHERE id = :id"), {**cambios, "id": f["id"]})
            n += 1
    # Instantáneas de historial de pacientes guardadas en claro antes de R27
    hist = conn.execute(text("SELECT id, previous_data, new_data FROM record_history WHERE entity_type = 'PATIENT'")).mappings().all()
    for h in hist:
        nuevos = {}
        for col in ("previous_data", "new_data"):
            datos = dict(h[col] or {})
            tocado = False
            for campo in CAMPOS_PACIENTE:
                v = datos.get(campo)
                if v is not None and not str(v).startswith(PREFIJO):
                    datos[campo] = cifrar_campo(v, f"patients.{campo}")
                    tocado = True
            if tocado:
                nuevos[col] = datos
        if nuevos:
            import json
            sets = ", ".join(f"{k} = CAST(:{k} AS JSONB)" for k in nuevos)
            conn.execute(text(f"UPDATE record_history SET {sets} WHERE id = :id"),
                         {**{k: json.dumps(v, ensure_ascii=False) for k, v in nuevos.items()}, "id": h["id"]})
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_patients_document_hash ON patients(document_hash)"))
    return n


def _limpiar_auditoria_previa(conn) -> None:
    """Antes de sellar: la auditoría del Corte 2 guardaba el documento en claro. Se protege (solo registros sin sellar)."""
    for clave in SENSIBLES_EN_DETALLE:
        conn.execute(text("UPDATE audit_log SET details = jsonb_set(details, ARRAY[:k], '\"<protegido>\"'::jsonb) "
                          "WHERE seq IS NULL AND details ? :k"), {"k": clave})


def asegurar_esquema_interop() -> None:
    from app.security.cadena import sellar_existentes

    with engine.begin() as conn:
        conn.exec_driver_sql("SELECT pg_advisory_xact_lock(724305)")
        for nombre in MIGRACIONES:
            conn.exec_driver_sql((CARPETA / nombre).read_text(encoding="utf-8"))
        cifrados = _cifrar_existentes(conn)
        _limpiar_auditoria_previa(conn)
        sellados = sellar_existentes(conn)
    if cifrados or sellados:
        log.info("Seguridad: %s pacientes cifrados (R27), %s registros de auditoría sellados (R28)", cifrados, sellados)
