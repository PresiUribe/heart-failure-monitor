"""Verificación automática del RAG (Semana 12).

Uso (Docker local, por Nginx):
    python scripts/rag_check.py --base http://localhost:5173/api
Contra el túnel:
    python scripts/rag_check.py --base https://xxxx.trycloudflare.com/api

Requiere: pip install httpx, y haber corrido scripts/seed_demo_api.py.
Crea un paciente "canario" con nombre, documento y teléfono conocidos, le genera un reporte
aprobado, uno rechazado y uno pendiente, y comprueba:
  1. Canario: ningún dato personal llega al índice, al prompt ni a la auditoría.
  2. Filtros: pendientes, rechazados y otros pacientes nunca aparecen en las fuentes.
  3. Rol: contable y paciente reciben 403.
  4. Citas y abstención.
  5. Auditoría de la consulta.
Al final imprime la tabla Recall@1 / Recall@3 / MRR para docs/RAG.md.
"""
import argparse
import json
import random
import sys

import httpx

USERS = {
    "admin": ("admin.demo", "AdminDemo2026!"),
    "medico": ("medico.demo", "MedicoDemo2026!"),
    "paciente": ("paciente.demo", "PacienteDemo2026!"),
    "contable": ("contable.demo", "ContableDemo2026!"),
}
CRITICO = {"age": 72, "anaemia": 1, "creatinine_phosphokinase": 250, "diabetes": 1, "ejection_fraction": 20,
           "high_blood_pressure": 1, "platelets": 250000, "serum_creatinine": 2.3, "serum_sodium": 126,
           "sex": 1, "smoking": 0, "time": 30}
resultados = []


def check(nombre, ok, detalle=""):
    resultados.append(bool(ok))
    print(f"[{'OK ' if ok else 'FALLA'}] {nombre}" + (f" -> {detalle}" if detalle and not ok else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5173/api")
    a = ap.parse_args()
    B = a.base.rstrip("/")
    c = httpx.Client(timeout=120)

    H = {}
    for rol, (u, p) in USERS.items():
        r = c.post(f"{B}/auth/login", data={"username": u, "password": p})
        if r.status_code != 200:
            sys.exit(f"No se pudo iniciar sesión con {u}: {r.status_code} {r.text[:200]}")
        H[rol] = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # 1. Canario sintético
    r = c.get(f"{B}/rag/canario", headers=H["admin"])
    jc = r.json() if r.status_code == 200 else {}
    check("Canario sintético y escaneo del índice", jc.get("ok"),
          f"sintética ok={jc.get('prueba_sintetica', {}).get('ok')} | pacientes con fuga={jc.get('pacientes_con_fuga')}")

    # Paciente canario con datos personales conocidos
    doc = str(random.randint(10**9, 2 * 10**9))
    nombre, apellido, tel = "Canaria", "Zuluaga Pérez", "3157778899"
    r = c.post(f"{B}/patients", headers=H["medico"], json={"documento": doc, "nombre": nombre, "apellido": apellido})
    if r.status_code not in (200, 201):
        sys.exit(f"No se pudo crear el paciente canario: {r.status_code} {r.text[:200]}")
    pid = r.json()["id"]
    canarios = [nombre, "Zuluaga", "Pérez", "Perez", doc, tel]

    rep = lambda body: c.post(f"{B}/patients/{pid}/ai/analyze", headers=H["medico"], json=body).json()["id"]  # noqa: E731
    aprobado, rechazado, pendiente = rep(CRITICO), rep({**CRITICO, "ejection_fraction": 45}), rep(CRITICO)
    c.post(f"{B}/ai/reports/{aprobado}/approve", headers=H["medico"],
           json={"observaciones": f"{nombre} {apellido}, CC {doc}, celular {tel}: hiponatremia y FEVI muy baja, remitir."})
    c.post(f"{B}/ai/reports/{rechazado}/reject", headers=H["medico"], json={"observaciones": "Datos de entrada incompletos."})

    # 2. Pregunta del médico sobre su paciente
    pregunta = f"¿Qué riesgo y qué criterios críticos tiene {nombre} {apellido} según su historia?"
    r = c.post(f"{B}/rag/preguntar", headers=H["medico"], json={"pregunta": pregunta, "paciente_id": pid})
    check("Médico consulta el RAG (200)", r.status_code == 200, r.text[:300])
    j = r.json() if r.status_code == 200 else {}
    fuentes = j.get("fuentes", [])
    alias = j.get("seudonimo")
    check("Respuesta con citas [n] y fuentes", fuentes and "[1]" in j.get("respuesta", ""), json.dumps(j, ensure_ascii=False)[:300])
    check("Solo fuentes vigentes o aprobadas (sin pendientes ni rechazados)",
          all(f["estado"] in ("vigente", "aprobado") for f in fuentes), str(fuentes))
    prohibidos = {"R-" + x.replace("-", "")[:12] for x in (rechazado, pendiente)}
    check("Ningún fragmento del reporte pendiente o rechazado", not any(f["doc_id"] in prohibidos for f in fuentes), str(fuentes))
    check("La historia citada es solo la de este paciente (seudónimo)",
          alias and all(f["tipo"] != "historia" or f.get("paciente") == alias for f in fuentes), str(fuentes))
    check("La respuesta no contiene datos personales", not any(x.lower() in j.get("respuesta", "").lower() for x in canarios))

    # 3. Rol
    for rol in ("contable", "paciente"):
        r = c.post(f"{B}/rag/preguntar", headers=H[rol], json={"pregunta": "¿qué es la hiponatremia?"})
        check(f"Rol {rol} -> 403", r.status_code == 403, str(r.status_code))
    r = c.post(f"{B}/rag/preguntar", json={"pregunta": "¿qué es la hiponatremia?"})
    check("Sin token -> 401", r.status_code == 401, str(r.status_code))

    # 4. Abstención
    r = c.post(f"{B}/rag/preguntar", headers=H["medico"], json={"pregunta": "¿quién ganó el mundial de fútbol de 2014?"})
    jj = r.json()
    check("Abstención fuera del dominio", jj.get("abstencion") and not jj.get("fuentes"), json.dumps(jj, ensure_ascii=False)[:300])
    r = c.post(f"{B}/rag/preguntar", headers=H["medico"], json={"pregunta": "¿desde qué valor de sodio se habla de hiponatremia?"})
    jj = r.json()
    check("Pregunta de guía responde con G05", any(f["doc_id"] == "G05" for f in jj.get("fuentes", [])), json.dumps(jj, ensure_ascii=False)[:300])

    # 5. Auditoría: la consulta queda registrada y sin datos personales
    logs = c.get(f"{B}/audit/logs?limit=200", headers=H["admin"]).json()
    rag = [x for x in logs if x["action"] == "RAG_QUERY" and (x.get("details") or {}).get("paciente_id") == pid]
    check("Consulta RAG en la auditoría", bool(rag))
    if rag:
        det = json.dumps(rag[0]["details"], ensure_ascii=False).lower()
        check("Prompt y pregunta auditados sin datos personales", not any(x.lower() in det for x in canarios),
              [x for x in canarios if x.lower() in det])
        check("El prompt auditado usa el seudónimo", alias and alias.lower() in det)

    # Evaluación de la recuperación
    r = c.get(f"{B}/rag/evaluacion", headers=H["admin"])
    if r.status_code == 200:
        e = r.json()
        print(f"\nModelo de embeddings: {e['modelo_embeddings']} | {e['n_preguntas']} preguntas | método configurado: {e['metodo_configurado']}\n")
        print(e["tabla_markdown"])
        dentro = [x["max_coseno"] for x in e["calibracion"]["en_dominio"]]
        fuera = [x["max_coseno"] for x in e["calibracion"]["fuera_de_dominio"]]
        print(f"\nCalibración (coseno máximo): en dominio min {min(dentro):.3f} / mediana {sorted(dentro)[len(dentro)//2]:.3f}"
              f" | fuera de dominio max {max(fuera):.3f} | umbral actual {e['umbrales']['coseno']}")
        u = e["umbrales"]
        pasa = lambda x: x["max_coseno"] >= u["coseno"] or x["max_bm25"] >= u["bm25"]  # noqa: E731
        abst_mal = [x["pregunta"] for x in e["calibracion"]["en_dominio"] if not pasa(x)]
        resp_mal = [x["pregunta"] for x in e["calibracion"]["fuera_de_dominio"] if pasa(x)]
        print(f"Con umbrales coseno={u['coseno']} / bm25={u['bm25']}: se abstendría en {len(abst_mal)} de "
              f"{len(dentro)} preguntas clínicas y respondería {len(resp_mal)} de {len(fuera)} ajenas")
        for q in abst_mal + resp_mal:
            print("   -", q)
    else:
        print("Evaluación no disponible:", r.status_code, r.text[:200])

    print(f"\n{sum(resultados)}/{len(resultados)} verificaciones OK")
    sys.exit(0 if all(resultados) else 1)


if __name__ == "__main__":
    main()
