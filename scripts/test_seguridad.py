"""Prueba de R27 (cifrado de campos) y R28 (auditoría encadenada). Corre DENTRO del contenedor de la API,
porque además de la API revisa directamente lo que quedó en PostgreSQL:

    docker compose exec api python scripts/test_seguridad.py

Las manipulaciones de la auditoría se hacen dentro de una transacción que se deshace al final:
la base queda como estaba.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.security import cadena  # noqa: E402
from app.security.campos import PREFIJO, cifrar_campo, descifrar_campo  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("API_URL", "http://localhost:8000")).rstrip("/")
USUARIOS = {"admin": ("admin.demo", "AdminDemo2026!"), "medico": ("medico.demo", "MedicoDemo2026!"),
            "contable": ("contable.demo", "ContableDemo2026!")}
resultados: list[tuple[bool, str]] = []


def check(nombre, ok, detalle=""):
    resultados.append((bool(ok), nombre))
    print(("  OK   " if ok else "  FALLA") + f" {nombre}" + ("" if ok or not detalle else f"\n         → {str(detalle)[:300]}"))
    return ok


def main() -> int:
    c = httpx.Client(timeout=30)
    H = {}
    for rol, (u, p) in USUARIOS.items():
        r = c.post(f"{BASE}/auth/login", data={"username": u, "password": p})
        if r.status_code == 200:
            H[rol] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    if "admin" not in H or "medico" not in H:
        print("Faltan los usuarios demo (python scripts/seed_demo_api.py)")
        return 1

    print("\n### R27 · Cifrado de campos sensibles (AES-256-GCM)")
    doc = f"99{secrets.randbelow(10**8):08d}"
    tel, direc, vereda = "3001234567", "Calle 5 # 10-20, Cali", "Vereda El Pinar"
    r = c.post(f"{BASE}/patients", headers=H["medico"], json={"documento": doc, "nombre": "Prueba Cifrado", "telefono": tel,
                                                              "direccion": direc, "vereda": vereda, "municipio": "Jamundí"})
    ok = check("El médico registra un paciente con documento, teléfono, dirección y vereda", r.status_code == 201, r.text)
    if not ok:
        return resumen()
    pac = r.json()
    check("La API los devuelve en claro a quien tiene permiso", pac["document_number"] == doc and pac["phone"] == tel and pac["address"] == direc, pac)
    with SessionLocal() as db:
        fila = db.execute(text("SELECT document_number, phone, address, rural_area, document_hash FROM patients WHERE id = :i"), {"i": pac["id"]}).mappings().one()
        crudo = " ".join(str(v) for v in fila.values())
        check("En PostgreSQL los cuatro campos están cifrados (gcm1:…) y no aparece el valor", all(str(fila[k]).startswith(PREFIJO)
              for k in ("document_number", "phone", "address", "rural_area")) and doc not in crudo and tel not in crudo and "Pinar" not in crudo, dict(fila))
        check("Dos cifrados del mismo dato son distintos (nonce aleatorio)",
              cifrar_campo(doc, "patients.document_number") != cifrar_campo(doc, "patients.document_number"))
        try:
            descifrar_campo(fila["phone"], "patients.address")
            movido = False
        except Exception:
            movido = True
        check("Un cifrado copiado a otra columna no se descifra (columna como dato asociado)", movido)
        check("El documento tiene índice ciego (HMAC) para buscar sin descifrar", fila["document_hash"] and len(fila["document_hash"]) == 64)
        det = db.execute(text("SELECT details::text FROM audit_log WHERE entity_id = :i AND action = 'CREATE'"), {"i": pac["id"]}).scalar() or ""
        check("La auditoría del alta no guarda el documento", doc not in det, det)
    r = c.get(f"{BASE}/patients/{doc}", headers=H["medico"])
    check("Se puede buscar al paciente por documento (índice ciego)", r.status_code == 200 and r.json()["id"] == pac["id"], r.text)
    r = c.post(f"{BASE}/patients", headers=H["medico"], json={"documento": f" {doc} ", "nombre": "Duplicado Prueba"})
    check("El mismo documento otra vez → 409 (unicidad sobre el índice ciego)", r.status_code == 409, r.text)
    r = c.put(f"{BASE}/patients/{pac['id']}", headers=H["admin"], json={"phone": "3109876543"})
    if r.status_code == 200:
        with SessionLocal() as db:
            h = db.execute(text("SELECT previous_data::text || new_data::text FROM record_history WHERE entity_id = :i ORDER BY changed_at DESC LIMIT 1"),
                           {"i": pac["id"]}).scalar() or ""
        check("El historial de cambios guarda las instantáneas cifradas", doc not in h and tel not in h and "3109876543" not in h, h[:200])
    if not os.environ.get("CAMPOS_CLAVE"):
        print("         (aviso: CAMPOS_CLAVE no está en el entorno; se usa la derivada de JWT_SECRET_KEY. Defínala en .env)")

    print("\n### R28 · Auditoría a prueba de manipulación (cadena de hashes)")
    r = c.get(f"{BASE}/audit/verificar", headers=H["admin"])
    check("GET /audit/verificar (admin): la cadena está íntegra", r.status_code == 200 and r.json()["integra"], r.text)
    n0 = r.json().get("registros", 0)
    r = c.get(f"{BASE}/audit/verificar", headers=H["medico"])
    check("Un médico no puede verificarla (403)", r.status_code == 403, r.text)
    if "contable" in H:
        check("Un contable tampoco (403)", c.get(f"{BASE}/audit/verificar", headers=H["contable"]).status_code == 403)
    r = c.get(f"{BASE}/audit/logs?limit=5", headers=H["admin"])
    ult = r.json()[0] if r.status_code == 200 and r.json() else {}
    check("Cada registro trae seq, prev_hash y hash", ult.get("seq") and len(ult.get("hash") or "") == 64 and len(ult.get("prev_hash") or "") == 64, ult)

    async def rafaga():
        async with httpx.AsyncClient(timeout=30) as ac:
            await asyncio.gather(*[ac.get(f"{BASE}/audit/verificar", headers=H["admin"]) for _ in range(12)],
                                 *[ac.post(f"{BASE}/auth/login", data={"username": "medico.demo", "password": "MedicoDemo2026!"}) for _ in range(8)])
    asyncio.run(rafaga())
    r = c.get(f"{BASE}/audit/verificar", headers=H["admin"]).json()
    check("Con 20 peticiones concurrentes la cadena sigue íntegra y sin huecos", r["integra"] and r["registros"] > n0, r)

    with engine.connect() as conn:
        tx = conn.begin()
        try:
            sp = conn.begin_nested()
            try:
                conn.execute(text("UPDATE audit_log SET details = '{}'::jsonb WHERE seq = 1"))
                bloqueado = False
            except Exception:
                bloqueado = True
            sp.rollback()
            check("Capa 1: la base no deja modificar un registro sellado (disparador)", bloqueado)
            sp = conn.begin_nested()
            try:
                conn.execute(text("DELETE FROM audit_log WHERE seq = 1"))
                bloqueado = False
            except Exception:
                bloqueado = True
            sp.rollback()
            check("Capa 1: tampoco deja borrarlo", bloqueado)

            # Capa 2: alguien con permisos de dueño desactiva el disparador y altera el pasado
            conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_solo_agregar"))
            objetivo = conn.execute(text("SELECT seq FROM audit_log WHERE seq IS NOT NULL ORDER BY seq DESC OFFSET 5 LIMIT 1")).scalar()
            conn.execute(text("UPDATE audit_log SET details = jsonb_set(coalesce(details,'{}'::jsonb), '{alterado}', 'true') WHERE seq = :s"), {"s": objetivo})
            with SessionLocal(bind=conn) as db:
                v = cadena.verificar(db)
            check(f"Capa 2: alterar el registro {objetivo} se detecta (contenido_alterado en ese seq)",
                  not v["integra"] and v["primer_problema"]["seq"] == objetivo and v["primer_problema"]["tipo"] == "contenido_alterado", v["primer_problema"])
            conn.execute(text("UPDATE audit_log SET hash = repeat('0', 64) WHERE seq = :s"), {"s": objetivo})
            with SessionLocal(bind=conn) as db:
                v = cadena.verificar(db)
            check("Recalcular el hash sin la llave no sirve: se rompe el enlace con el siguiente", not v["integra"], v["primer_problema"])
            conn.execute(text("DELETE FROM audit_log WHERE seq = :s"), {"s": objetivo - 2})
            with SessionLocal(bind=conn) as db:
                v = cadena.verificar(db)
            check("Borrar un registro se detecta (faltan_registros)", any(p["tipo"] == "faltan_registros" for p in v["problemas"]), v["problemas"][:3])
        finally:
            tx.rollback()  # todo lo anterior se deshace: la base queda intacta
    r = c.get(f"{BASE}/audit/verificar", headers=H["admin"]).json()
    check("Tras deshacer la manipulación, la cadena vuelve a estar íntegra", r["integra"], r)
    return resumen()


def resumen() -> int:
    ok = sum(1 for x, _ in resultados if x)
    print(f"\nResultado: {ok}/{len(resultados)}")
    for x, n in resultados:
        if not x:
            print(f"  · falló: {n}")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
