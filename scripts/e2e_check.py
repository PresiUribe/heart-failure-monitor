"""Verificación de punta a punta del Corte 2 (R01–R21), al estilo del tester.

Uso (Docker local, pasando por Nginx como lo hará el tester):
    python scripts/e2e_check.py --base http://localhost:5173/api --fhir http://localhost:5173/fhir

Uso contra la URL pública del túnel:
    python scripts/e2e_check.py --base https://xxxx.trycloudflare.com/api --fhir https://xxxx.trycloudflare.com/fhir

Requiere: pip install httpx websockets, y haber corrido scripts/seed_demo_api.py.
Por defecto escucha el canal WebSocket (--canal ws); con --canal sse usa SSE (solo local). Crea un usuario de
prueba "bloqueo.demo" para R02 (no bloquea las cuentas de la demo).
"""
import argparse
import json
import queue
import sys
import threading
import time
import uuid

import httpx
from concurrent.futures import ThreadPoolExecutor

USERS = {
    "admin": ("admin.demo", "AdminDemo2026!"),
    "medico": ("medico.demo", "MedicoDemo2026!"),
    "especialista": ("especialista.demo", "EspecialistaDemo2026!"),
    "paciente": ("paciente.demo", "PacienteDemo2026!"),
    "contable": ("contable.demo", "ContableDemo2026!"),
}
CRITICO = {"age": 72, "anaemia": 1, "creatinine_phosphokinase": 250, "diabetes": 1, "ejection_fraction": 20,
           "high_blood_pressure": 1, "platelets": 250000, "serum_creatinine": 2.3, "serum_sodium": 126,
           "sex": 1, "smoking": 0, "time": 30}

results = []


def check(rid, name, ok, detail=""):
    results.append((rid, name, bool(ok), detail))
    print(f"[{'OK ' if ok else 'FALLA'}] {rid} {name}" + (f" -> {detail}" if detail and not ok else ""))


class WS:
    """Escucha el canal WebSocket en un hilo (misma interfaz que SSE)."""

    def __init__(self, base, token):
        self.events = queue.Queue()
        self.all = []
        self.url = base.replace("https://", "wss://").replace("http://", "ws://") + f"/events/ws?token={token}"
        self.status = None
        threading.Thread(target=self._run, daemon=True).start()
        time.sleep(2.0)

    def _run(self):
        from websockets.sync.client import connect
        try:
            with connect(self.url, open_timeout=15) as ws:
                self.status = 200
                for msg in ws:
                    ev = json.loads(msg)
                    self.all.append(ev)
                    self.events.put(ev)
        except Exception as exc:  # noqa: BLE001
            if self.status is None:
                self.status = f"error {exc}"

    wait_for = None  # se asigna abajo (igual que SSE)


class SSE:
    """Escucha el canal SSE en un hilo y guarda los eventos JSON recibidos."""

    def __init__(self, base, token):
        self.events = queue.Queue()
        self.all = []
        self.url = f"{base}/events/stream?token={token}"
        self.status = None
        threading.Thread(target=self._run, daemon=True).start()
        time.sleep(1.5)

    def _run(self):
        try:
            with httpx.stream("GET", self.url, timeout=None) as r:
                self.status = r.status_code
                for line in r.iter_lines():
                    if line.startswith("data: "):
                        ev = json.loads(line[6:])
                        self.all.append(ev)
                        self.events.put(ev)
        except Exception as exc:  # noqa: BLE001
            self.status = f"error {exc}"

    def wait_for(self, pred, timeout=8.0):
        end = time.time() + timeout
        for ev in self.all:
            if pred(ev):
                return ev
        while time.time() < end:
            try:
                ev = self.events.get(timeout=max(0.1, end - time.time()))
            except queue.Empty:
                break
            if pred(ev):
                return ev
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5173/api")
    ap.add_argument("--fhir", default="http://localhost:5173/fhir")
    ap.add_argument("--skip-ratelimit", action="store_true")
    ap.add_argument("--canal", choices=["ws", "sse"], default="ws")
    a = ap.parse_args()
    B = a.base.rstrip("/")
    c = httpx.Client(timeout=30)

    def login(user, pwd):
        return c.post(f"{B}/auth/login", data={"username": user, "password": pwd})

    tok = {}
    for rol, (u, p) in USERS.items():
        r = login(u, p)
        if r.status_code != 200:
            print(f"No se pudo iniciar sesión con {u}: {r.status_code} {r.text[:200]}")
            sys.exit(1)
        tok[rol] = r.json()["access_token"]
    H = {rol: {"Authorization": f"Bearer {t}"} for rol, t in tok.items()}

    # R01
    roles = {rol: c.get(f"{B}/yo", headers=H[rol]).json().get("rol") for rol in USERS}
    check("R01", "5 roles distintos vía /yo", len(set(roles.values())) >= 5 and all(roles[k] == k for k in roles), str(roles))

    # R03
    check("R03", "sin token -> 401/403", c.get(f"{B}/patients").status_code in (401, 403))
    check("R03", "médico pide auditoría -> 403", c.get(f"{B}/audit/logs", headers=H["medico"]).status_code == 403)
    r = c.options(f"{B}/patients", headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"})
    acao = r.headers.get("access-control-allow-origin")
    check("R03", "CORS no refleja origen desconocido", acao not in ("*", "https://evil.example.com"), str(acao))
    r = c.get(f"{B}/yo", headers=H["medico"])
    check("R03", "cabeceras nosniff + XFO/CSP", r.headers.get("x-content-type-options") == "nosniff" and (r.headers.get("x-frame-options") or r.headers.get("content-security-policy")))

    # SSE
    Canal = WS if a.canal == "ws" else SSE
    sse = {rol: Canal(B, tok[rol]) for rol in ("medico", "especialista", "paciente", "contable", "admin")}
    if a.canal == "ws":
        from websockets.sync.client import connect
        try:
            with connect(B.replace("https://", "wss://").replace("http://", "ws://") + "/events/ws", open_timeout=10) as ws:
                ws.recv(timeout=3)
            rechazado = False
        except Exception:  # noqa: BLE001
            rechazado = True
        check("R17", "WebSocket sin token rechazado", rechazado)
    else:
        r = c.get(f"{B}/events/stream", timeout=5)
        check("R17", "SSE sin token -> 401/403/422", r.status_code in (401, 403, 422), str(r.status_code))
    check("R17", f"canal {a.canal} conecta con ?token=", all(s.status == 200 for s in sse.values()), str({k: s.status for k, s in sse.items()}))

    # Paciente canario
    canario = f"CANARIO{uuid.uuid4().hex[:6].upper()}"
    body = {"document_number": f"DOC-{canario}", "first_name": canario, "last_name": "Prueba",
            "email": f"{canario.lower()}@correo.co", "telefono": "3001234567", "direccion": f"Calle {canario}",
            "sex": "MALE", "birth_date": "1954-01-01", "primary_diagnosis": "Insuficiencia cardiaca"}
    r = c.post(f"{B}/patients", json=body, headers=H["medico"])
    check("R01", "médico crea paciente", r.status_code == 201, r.text[:200])
    pat = r.json()
    pid = pat["id"]

    # R15
    fhir_id = pat.get("fhir_patient_id")
    if fhir_id:
        fr = c.get(f"{a.fhir.rstrip('/')}/Patient", params={"identifier": pat["document_number"]})
        check("R15", "paciente encontrado en FHIR por identificador", fr.status_code == 200 and fr.json().get("total", len(fr.json().get("entry", []))) >= 1, fr.text[:200])
    else:
        check("R15", "paciente sincronizado a FHIR al crearse", False, "fhir_patient_id vacío (¿HAPI arriba?)")

    # R10 + R12 + R20
    r = c.post(f"{B}/patients/{pid}/ai/analyze", json=CRITICO, headers=H["medico"])
    rep = r.json()
    check("R10", "analizar -> estado pendiente", r.status_code in (200, 201) and rep.get("estado") == "pendiente", r.text[:200])
    ev = sse["medico"].wait_for(lambda e: "alerta" in e.get("tipo", "") or "critic" in e.get("tipo", ""))
    check("R20", "alerta crítica llega en vivo al médico", ev is not None)
    check("R20", "alerta NO llega al paciente/contable", not any("alerta" in e.get("tipo", "") for e in sse["paciente"].all + sse["contable"].all))
    pl = c.get(f"{B}/ai/reports/{rep['id']}/payload", headers=H["medico"]).json()
    txt = json.dumps(pl).lower()
    check("R12", "payload IA sin datos personales canario", canario.lower() not in txt and "3001234567" not in txt and "correo.co" not in txt and "prueba" not in txt, txt[:200])
    pend = c.get(f"{B}/pending", headers=H["medico"]).json()
    check("R18", "pendientes: reportes y alertas suben", pend["reportes_por_revisar"] >= 1 and pend["alertas_activas"] >= 1, str(pend))

    alerts = c.get(f"{B}/alerts", headers=H["medico"]).json()
    al = next((x for x in alerts if x["paciente_id"] == pid and x["estado"] == "activa"), None)
    check("R20", "alerta activa listada", al is not None)
    check("R20", "paciente/contable no ven alertas", c.get(f"{B}/alerts", headers=H["paciente"]).status_code == 403 and c.get(f"{B}/alerts", headers=H["contable"]).status_code == 403)
    if al:
        check("R20", "contable no reconoce", c.post(f"{B}/alerts/{al['id']}/acknowledge", json={"nota": "x"}, headers=H["contable"]).status_code == 403)
        r = c.post(f"{B}/alerts/{al['id']}/acknowledge", json={"nota": "Paciente valorado"}, headers=H["medico"])
        check("R20", "médico reconoce alerta", r.status_code == 200 and r.json().get("estado") == "reconocida", r.text[:200])

    # R10 aprobar / rechazar + R19 factura
    check("R10", "paciente no aprueba -> 403", c.post(f"{B}/ai/reports/{rep['id']}/approve", json={}, headers=H["paciente"]).status_code == 403)
    check("R10", "contable no aprueba -> 403", c.post(f"{B}/ai/reports/{rep['id']}/approve", json={}, headers=H["contable"]).status_code == 403)
    r = c.post(f"{B}/ai/reports/{rep['id']}/approve", json={"observaciones": "Concordante con clínica"}, headers=H["medico"])
    check("R10", "médico aprueba", r.status_code == 200 and r.json().get("estado") == "aprobado", r.text[:200])
    ev = sse["contable"].wait_for(lambda e: e.get("tipo") == "factura_automatica")
    check("R19", "contable recibe evento de factura en vivo", ev is not None)
    inv = c.get(f"{B}/billing/invoices", headers=H["contable"]).json()
    check("R19", "factura automática con reporte_id y paciente_id", any(x.get("reporte_id") == rep["id"] and x.get("paciente_id") == pid for x in inv))
    r2 = c.post(f"{B}/patients/{pid}/ai/analyze", json={**CRITICO, "ejection_fraction": 55, "serum_creatinine": 0.9, "serum_sodium": 140, "age": 45}, headers=H["medico"]).json()
    r = c.post(f"{B}/ai/reports/{r2['id']}/reject", json={"observaciones": "No concordante"}, headers=H["medico"])
    check("R10", "médico rechaza", r.json().get("estado") == "rechazado")
    check("R10", "rechazado se conserva", c.get(f"{B}/ai/reports/{r2['id']}", headers=H["medico"]).json().get("estado") == "rechazado")
    hist = c.get(f"{B}/patients/{pid}/history", headers=H["medico"]).json()
    ids = [x["id"] for x in hist.get("reportes_ia", [])]
    check("R10", "historia solo trae aprobados", rep["id"] in ids and r2["id"] not in ids, str(ids))
    check("R03", "contable no ve historia", c.get(f"{B}/patients/{pid}/history", headers=H["contable"]).status_code in (403, 404))
    pdf = c.get(f"{B}/patients/{pid}/history.pdf", headers=H["medico"])
    check("R11", "PDF con nombre del paciente", pdf.headers.get("content-type", "").startswith("application/pdf") and canario.encode() in pdf.content)

    # R09
    r = c.post(f"{B}/billing/invoices", json={"paciente_id": pid, "servicio": "Consulta", "valor": 50000}, headers=H["contable"])
    check("R09", "contable crea factura", r.status_code in (200, 201), r.text[:200])
    check("R09", "médico no accede a contabilidad", c.get(f"{B}/billing/invoices", headers=H["medico"]).status_code == 403)

    # R08 + R19
    check("R08", "paciente no remite", c.post(f"{B}/patients/{pid}/referrals", json={"motivo": "x x x"}, headers=H["paciente"]).status_code == 403)
    r = c.post(f"{B}/patients/{pid}/referrals", json={"especialista_id": USERS["especialista"][0], "motivo": "Valoración cardiología"}, headers=H["medico"])
    ref = r.json()
    check("R08", "médico remite -> id", r.status_code in (200, 201) and ref.get("id"), r.text[:200])
    ev = sse["especialista"].wait_for(lambda e: e.get("tipo") == "remision_recibida" and e.get("paciente_id") == pid)
    check("R17", "remisión llega en vivo al especialista (<8 s)", ev is not None)
    check("R19", "paciente/contable no reciben la remisión", not any(e.get("tipo") == "remision_recibida" for e in sse["paciente"].all + sse["contable"].all))
    pe = c.get(f"{B}/pending", headers=H["especialista"]).json()
    check("R18", "contador remisiones_recibidas sube", pe["remisiones_recibidas"] >= 1, str(pe))
    lst = c.get(f"{B}/referrals", headers=H["especialista"]).json()
    check("R08", "especialista ve remisión con paciente_id", any(x["id"] == ref["id"] and x["paciente_id"] == pid for x in lst))
    r = c.post(f"{B}/referrals/{ref['id']}/accept", headers=H["especialista"])
    check("R08", "especialista acepta", r.status_code == 200)
    lst = c.get(f"{B}/referrals", headers=H["especialista"]).json()
    check("R08", "remisión queda 'aceptada'", any(x["id"] == ref["id"] and x["estado"] == "aceptada" for x in lst))
    ev = sse["medico"].wait_for(lambda e: e.get("tipo") == "remision_aceptada")
    check("R19", "médico recibe aviso de aceptación", ev is not None)
    pe2 = c.get(f"{B}/pending", headers=H["especialista"]).json()
    check("R18", "contador remisiones baja al aceptar", pe2["remisiones_recibidas"] == pe["remisiones_recibidas"] - 1, str(pe2))

    # R18 bandeja
    for rol in USERS:
        r = c.get(f"{B}/notifications", headers=H[rol])
        check("R18", f"bandeja de {rol}", r.status_code == 200 and isinstance(r.json(), list))
    notif = c.get(f"{B}/notifications", headers=H["especialista"]).json()
    if notif:
        nid = notif[0]["id"]
        check("R18", "otro usuario no marca notificación ajena", c.post(f"{B}/notifications/{nid}/read", headers=H["medico"]).status_code in (403, 404))
        c.post(f"{B}/notifications/{nid}/read", headers=H["especialista"])
        check("R18", "dueño marca como leída", any(n["id"] == nid and n["leida"] for n in c.get(f"{B}/notifications", headers=H["especialista"]).json()))

    # R03 paciente ajeno
    check("R03", "paciente no ve a otro paciente", c.get(f"{B}/patients/{pid}/history", headers=H["paciente"]).status_code in (403, 404))

    # R07
    cl = c.get(f"{B}/clusters", headers=H["medico"]).json()
    check("R07", ">=2 clusters con reales y sintéticos", len(cl) >= 2 and all(x["n_sinteticos"] > 0 and x["descripcion"] for x in cl))

    # R06
    imgs = c.get(f"{B}/patients/{pid}/images", headers=H["medico"]).json()
    demo = [p for p in c.get(f"{B}/patients", headers=H["medico"]).json() if p["document_number"] == "DEMO-HF-001"]
    if demo:
        imgs = c.get(f"{B}/patients/{demo[0]['id']}/images", headers=H["medico"]).json()
    check("R06", "paciente demo tiene imagen real", len(imgs) >= 1, "Importa CAMUS con scripts/import_camus_folder.py")
    if imgs:
        check("R06", "imagen descargable", c.get(f"{B}/images/{imgs[0]['id']}", headers=H["medico"]).status_code == 200)

    # R21
    obs = {"resourceType": "Observation", "status": "final",
           "code": {"coding": [{"system": "http://loinc.org", "code": "2339-0", "display": "Glucose [Mass/volume] in Blood"}]},
           "subject": {"reference": f"Patient/{fhir_id or pid}"},
           "valueQuantity": {"value": 182, "unit": "mg/dL", "system": "http://unitsofmeasure.org", "code": "mg/dL"}}
    r = c.post(f"{a.fhir.rstrip('/')}/Observation", json=obs, headers={"Content-Type": "application/fhir+json"})
    check("R21", "FHIR acepta POST Observation", r.status_code in (200, 201), r.text[:200])
    ev = sse["medico"].wait_for(lambda e: e.get("tipo") == "fhir_observation" and e.get("paciente_id") == pid, timeout=10)
    check("R21", "médico recibe aviso de Observation FHIR", ev is not None)

    # R14
    r = c.delete(f"{B}/patients/{pid}", headers=H["medico"])
    check("R14", "médico hace soft delete de su paciente", r.status_code == 200, r.text[:200])
    check("R14", "médico no restaura", c.post(f"{B}/patients/{pid}/restore", headers=H["medico"]).status_code == 403)
    check("R14", "admin restaura", c.post(f"{B}/patients/{pid}/restore", headers=H["admin"]).status_code == 200)

    # R02 + R19
    test_user = "bloqueo.demo"
    users = c.get(f"{B}/users?include_deleted=true", headers=H["admin"]).json()
    if not any(u["username"] == test_user for u in users):
        c.post(f"{B}/users", headers=H["admin"], json={"document_type": "CC", "document_number": "1000009999", "full_name": "Cuenta bloqueo",
                                                       "username": test_user, "email": "bloqueo@hfmonitor.co", "password": "BloqueoDemo2026!", "role_code": "CONTABLE"})
    for _ in range(3):
        login(test_user, "mala-clave")
    r = login(test_user, "BloqueoDemo2026!")
    check("R02", "4.º intento correcto rechazado", r.status_code in (401, 403, 423, 429), str(r.status_code))
    ev = sse["admin"].wait_for(lambda e: e.get("tipo") == "cuenta_bloqueada")
    check("R19", "admin recibe aviso de bloqueo", ev is not None)
    r = c.post(f"{B}/users/{test_user}/unlock", headers=H["admin"])
    check("R02", "admin desbloquea", r.status_code == 200)
    check("R02", "login correcto tras desbloqueo", login(test_user, "BloqueoDemo2026!").status_code == 200)

    # R13
    logs = c.get(f"{B}/audit/logs?limit=500", headers=H["admin"])
    txt = logs.text
    check("R13", "auditoría con paciente y reporte de la prueba", logs.status_code == 200 and pid in txt and rep["id"] in txt, str(logs.status_code))
    acts = {x["action"] for x in logs.json()} if logs.status_code == 200 else set()
    need = {"LOGIN_FAILED", "ACCOUNT_LOCKED", "ACCOUNT_UNLOCKED", "CREATE", "AI_ANALYSIS", "AI_REPORT_APPROVED", "AI_REPORT_REJECTED",
            "REFERRAL_CREATED", "INVOICE_CREATED", "CRITICAL_ALERT_CREATED", "CRITICAL_ALERT_ACKNOWLEDGED", "FHIR_EVENT_RECEIVED", "SOFT_DELETE", "RESTORE"}
    check("R13", "acciones requeridas auditadas", need <= acts, f"faltan {sorted(need - acts)}")

    # R03 rate limit (al final para no afectar lo demás)
    if not a.skip_ratelimit:
        # Ráfaga en paralelo (como un cliente agresivo): Nginx debe responder 429.
        def hit(_):
            try:
                return httpx.get(f"{B}/yo", headers=H["admin"], timeout=20).status_code
            except Exception:  # noqa: BLE001
                return 0
        with ThreadPoolExecutor(max_workers=40) as pool:
            codes = list(pool.map(hit, range(150)))
        check("R03", "ráfaga recibe 429", 429 in codes, f"{codes.count(429)} respuestas 429")

    ok = sum(1 for r in results if r[2])
    print(f"\n{ok}/{len(results)} verificaciones OK")
    for rid, name, passed, detail in results:
        if not passed:
            print(f"  FALLA {rid}: {name} {detail}")


WS.wait_for = SSE.wait_for


if __name__ == "__main__":
    main()
