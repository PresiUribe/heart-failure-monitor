"""Rota la llave de cifrado de campos (R27): descifra con la anterior y vuelve a cifrar con CAMPOS_CLAVE.

Uso (dentro del contenedor, que ya debe tener la llave NUEVA en su entorno):
    1. Ponga la nueva CAMPOS_CLAVE en .env y recree la API:   docker compose up -d
    2. Rote (la anterior va por variable; vacía = la que se derivaba de JWT_SECRET_KEY):
         docker compose exec api python scripts/rotar_clave_campos.py
         docker compose exec -e CAMPOS_CLAVE_ANTERIOR=la_vieja api python scripts/rotar_clave_campos.py
    3. Reinicie la API:                                       docker compose restart api

Todo ocurre en una transacción: si una fila no se puede descifrar con la llave anterior, no cambia nada.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402
from app.security.campos import PREFIJO, cifrar_campo, descifrar_campo, id_llave, indice_ciego, maestra_de  # noqa: E402

CAMPOS = ("document_number", "phone", "address", "rural_area")


def main() -> int:
    vieja = maestra_de(os.environ.get("CAMPOS_CLAVE_ANTERIOR") or None)
    nueva = maestra_de(settings.campos_clave or None)
    kid_viejo, kid_nuevo = id_llave(vieja), id_llave(nueva)
    if kid_viejo == kid_nuevo:
        print(f"La llave anterior y la actual son la misma ({kid_nuevo}): nada que rotar.")
        return 0
    if not settings.campos_clave:
        print("Aviso: CAMPOS_CLAVE no está en el entorno del contenedor; se rotará HACIA la llave derivada de JWT_SECRET_KEY.")

    def recifrar(valor, contexto):
        if valor is None or not str(valor).startswith(PREFIJO):
            return cifrar_campo(valor, contexto, nueva) if valor is not None else None
        if str(valor).split(":", 2)[1] == kid_nuevo:
            return valor
        return cifrar_campo(descifrar_campo(valor, contexto, vieja), contexto, nueva)

    pacientes = historial = 0
    with engine.begin() as conn:
        for f in conn.execute(text("SELECT id, document_number, phone, address, rural_area FROM patients")).mappings().all():
            nuevos = {c: recifrar(f[c], f"patients.{c}") for c in CAMPOS}
            doc = descifrar_campo(nuevos["document_number"], "patients.document_number", nueva)
            nuevos["document_hash"] = indice_ciego(doc, nueva)
            conn.execute(text("UPDATE patients SET document_number=:document_number, phone=:phone, address=:address, "
                              "rural_area=:rural_area, document_hash=:document_hash WHERE id=:id"), {**nuevos, "id": f["id"]})
            pacientes += 1
        for h in conn.execute(text("SELECT id, previous_data, new_data FROM record_history WHERE entity_type='PATIENT'")).mappings().all():
            cambios = {}
            for col in ("previous_data", "new_data"):
                datos = dict(h[col] or {})
                for c in CAMPOS:
                    if datos.get(c) is not None:
                        datos[c] = recifrar(datos[c], f"patients.{c}")
                cambios[col] = json.dumps(datos, ensure_ascii=False)
            conn.execute(text("UPDATE record_history SET previous_data=CAST(:previous_data AS JSONB), "
                              "new_data=CAST(:new_data AS JSONB) WHERE id=:id"), {**cambios, "id": h["id"]})
            historial += 1
    print(f"Llave rotada {kid_viejo} → {kid_nuevo}: {pacientes} pacientes y {historial} instantáneas de historial recifradas.")
    print("Ahora: docker compose restart api")
    return 0


if __name__ == "__main__":
    sys.exit(main())
