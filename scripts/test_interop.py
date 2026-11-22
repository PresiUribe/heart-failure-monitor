"""Prueba de la interoperabilidad contra una API en marcha (R29–R33), sin depender de otro equipo.

    python scripts/test_interop.py                       # API local (http://localhost:8000)
    python scripts/test_interop.py https://SU-TUNEL.trycloudflare.com/api

Crea dos instituciones de prueba que apuntan a nosotros mismos (una sin firma y otra con firma HMAC), así el
viaje completo pasa por la puerta real: token OAuth2 → Bundle → especialista acepta/rechaza → Task de vuelta.
Al final las desactiva. Usa los usuarios demo del tester.yaml.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
URL_INTERNA = "http://localhost:8000"   # cómo se ve la API a sí misma dentro del contenedor
USUARIOS = {"admin": ("admin.demo", "AdminDemo2026!"), "medico": ("medico.demo", "MedicoDemo2026!"),
            "especialista": ("especialista.demo", "EspecialistaDemo2026!")}
EJEMPLO_PULSO = Path(__file__).resolve().parents[1] / "docs" / "interop" / "pulso_bundle_remision_ejemplo.json"
TAG = uuid.uuid4().hex[:6]
resultados: list[tuple[bool, str]] = []


def check(nombre: str, ok: bool, detalle: str = "") -> bool:
    resultados.append((ok, nombre))
    print(("  OK   " if ok else "  FALLA") + f" {nombre}" + ("" if ok or not detalle else f"\n         → {detalle[:400]}"))
    return ok


def issues(r) -> list[str]:
    try:
        return [i.get("diagnostics", "") for i in r.json().get("issue", [])]
    except Exception:
        return []


def main() -> int:
    c = httpx.Client(timeout=60)
    H = {}
    for rol, (u, p) in USUARIOS.items():
        r = c.post(f"{BASE}/auth/login", data={"username": u, "password": p})
        if r.status_code != 200:
            print(f"No pude iniciar sesión como {u}: {r.status_code} {r.text[:200]}")
            return 1
        H[rol] = {"Authorization": f"Bearer {r.json()['access_token']}"}

    print("\n### 0. Instituciones de prueba (apuntan a nosotros mismos)")
    r = c.post(f"{BASE}/interop/socios", headers=H["admin"], json={"codigo": f"prueba-{TAG}", "nombre": f"Prueba {TAG}", "url_base": URL_INTERNA})
    check("Admin crea institución y recibe sus credenciales una sola vez", r.status_code == 201 and "client_secret" in r.json().get("credenciales_una_sola_vez", {}), r.text)
    cred = r.json()["credenciales_una_sola_vez"]
    socio = f"prueba-{TAG}"
    r = c.post(f"{BASE}/interop/socios/{socio}/salida", headers=H["admin"],
               json={"auth": "oauth2", "credencial_id": cred["client_id"], "credencial_secreto": cred["client_secret"]})
    check("Admin carga la credencial de salida (oauth2)", r.status_code == 200 and r.json()["lista_para_enviar"], r.text)
    r = c.get(f"{BASE}/interop/socios", headers=H["admin"])
    check("El listado de socios no expone secretos", r.status_code == 200 and cred["client_secret"] not in r.text, r.text[:200])
    r = c.get(f"{BASE}/interop/socios", headers=H["medico"])
    check("Un médico no administra socios (403)", r.status_code == 403, r.text)

    print("\n### 1. Autenticación entre sistemas (R31)")
    r = c.get(f"{BASE}/interop/metadata")
    check("GET /interop/metadata es público y es un CapabilityStatement", r.status_code == 200 and r.json().get("resourceType") == "CapabilityStatement", r.text)
    malo = c.post(f"{BASE}/oauth/token", data={"grant_type": "client_credentials", "client_id": cred["client_id"], "client_secret": "malo"})
    inexistente = c.post(f"{BASE}/oauth/token", data={"grant_type": "client_credentials", "client_id": "cli_no_existe", "client_secret": "x"})
    check("Secreto malo y cliente inexistente dan el MISMO 401 invalid_client", malo.status_code == inexistente.status_code == 401
          and malo.json() == inexistente.json(), f"{malo.text} | {inexistente.text}")
    r = c.post(f"{BASE}/oauth/token", data={"grant_type": "password", "client_id": cred["client_id"], "client_secret": cred["client_secret"]})
    check("grant_type distinto de client_credentials → 400 unsupported_grant_type", r.status_code == 400 and r.json()["error"] == "unsupported_grant_type", r.text)
    r = c.post(f"{BASE}/oauth/token", data={"grant_type": "client_credentials", "client_id": cred["client_id"], "client_secret": cred["client_secret"], "scope": "system/Patient.read"})
    check("Scope no asignado → 400 invalid_scope", r.status_code == 400 and r.json()["error"] == "invalid_scope", r.text)
    basic = base64.b64encode(f"{cred['client_id']}:{cred['client_secret']}".encode()).decode()
    r = c.post(f"{BASE}/oauth/token", data={"grant_type": "client_credentials"}, headers={"Authorization": f"Basic {basic}"})
    check("Token con HTTP Basic → 200 Bearer, 600 s, Cache-Control no-store", r.status_code == 200 and r.json()["expires_in"] == 600
          and r.headers.get("cache-control") == "no-store", r.text)
    token = r.json()["access_token"]
    T = {"Authorization": f"Bearer {token}", "Content-Type": "application/fhir+json"}
    r = c.post(f"{BASE}/oauth/token", data={"grant_type": "client_credentials", "client_id": cred["client_id"], "client_secret": cred["client_secret"], "scope": "task:write"})
    T_task = {"Authorization": f"Bearer {r.json()['access_token']}", "Content-Type": "application/fhir+json"}

    r = c.post(f"{BASE}/interop/Bundle", json={"resourceType": "Bundle"})
    check("Sin credencial → 401 OperationOutcome", r.status_code == 401 and r.json().get("resourceType") == "OperationOutcome", r.text)
    r = c.post(f"{BASE}/interop/Bundle", json={"resourceType": "Bundle"}, headers=H["medico"])
    check("Un token de USUARIO no abre la entrada de socios → 401", r.status_code == 401, r.text)
    r = c.get(f"{BASE}/patients", headers={"Authorization": f"Bearer {token}"})
    check("Un token de SISTEMA no abre la API clínica (/patients) → 401", r.status_code == 401, r.text)
    r = c.post(f"{BASE}/interop/Bundle", content=b"{}", headers=T_task)
    check("Token sin el scope del Bundle (solo task:write) → 403", r.status_code == 403, r.text)
    r = c.post(f"{BASE}/interop/Bundle", content=b"{}", headers={"X-API-Key": f"{cred['client_id']}.malo", "Content-Type": "application/json"})
    check("API key con secreto malo → 401", r.status_code == 401, r.text)

    print("\n### 2. Validación del recurso (R30): 400 / 422 / 413 / nonce, sin datos a medias")
    antes = len(c.get(f"{BASE}/remisiones/externas", headers=H["admin"]).json())
    r = c.post(f"{BASE}/interop/Bundle", content=b"{esto no es json", headers=T)
    check("Cuerpo que no es JSON → 400", r.status_code == 400, r.text)
    invalido = {"resourceType": "Bundle", "type": "transaction", "entry": [
        {"fullUrl": "urn:uuid:p", "resource": {"resourceType": "Patient", "identifier": [{"type": {"coding": [{"code": "CC"}]}, "value": "123"}], "birthDate": "09/07/1958"}},
        {"fullUrl": "urn:uuid:s", "resource": {"resourceType": "ServiceRequest", "status": "draft", "intent": "order", "subject": {"reference": "urn:uuid:otro"},
                                               "reasonCode": [{"text": "x"}], "priority": "ya"}},
        {"resource": {"resourceType": "Observation", "status": "final", "code": {"coding": [{"system": "urn:local", "code": "K"}]},
                      "subject": {"reference": "urn:uuid:p"}, "valueQuantity": {"value": "6,7"}}}]}
    r = c.post(f"{BASE}/interop/Bundle", json=invalido, headers=T)
    check("Bundle con varios errores → 422 con un issue por problema", r.status_code == 422 and len(issues(r)) >= 6, f"{r.status_code} {issues(r)}")
    con_preliminar = json.loads(EJEMPLO_PULSO.read_text(encoding="utf-8"))
    for e in con_preliminar["entry"]:
        if e["resource"]["resourceType"] == "DiagnosticReport":
            e["resource"]["status"] = "preliminary"
    r = c.post(f"{BASE}/interop/Bundle", json=con_preliminar, headers=T)
    check("DiagnosticReport de IA no aprobado (preliminary) → 422", r.status_code == 422 and any("APROBADO" in d for d in issues(r)), r.text)
    r = c.post(f"{BASE}/interop/Bundle", content=b'{"a":"' + b"x" * 1_100_000 + b'"}', headers=T)
    check("Más de 1 MB → 413", r.status_code == 413, f"{r.status_code} {r.text[:120]}")
    despues = len(c.get(f"{BASE}/remisiones/externas", headers=H["admin"]).json())
    check("Ningún rechazo dejó remisiones a medias", antes == despues, f"{antes} → {despues}")
    r = c.post(f"{BASE}/interop/Bundle", content=b"{}", headers={**T, "x-nonce": "corto"})
    check("x-nonce con formato inválido → 400", r.status_code == 400, r.text)

    print("\n### 3. El Bundle de Pulso (contrato común) entra tal cual")
    ejemplo = json.loads(EJEMPLO_PULSO.read_text(encoding="utf-8"))
    for e in ejemplo["entry"]:
        res = e["resource"]
        if res["resourceType"] in ("ServiceRequest", "Task"):
            res["identifier"][0]["value"] = f"66{TAG}"
    nonce = secrets.token_urlsafe(18)
    api_key = {"X-API-Key": cred["api_key"], "Content-Type": "application/fhir+json", "x-nonce": nonce}
    r = c.post(f"{BASE}/interop/Bundle", json=ejemplo, headers=api_key)
    ok = check("POST /interop/Bundle (API key) → 201 Task requested + Location", r.status_code == 201 and r.json().get("status") == "requested"
               and r.headers.get("location", "").startswith("/interop/Task/rem-"), r.text)
    if ok:
        loc = r.headers["location"]
        check("El Task apunta a SU ServiceRequest y a SU Task (rem-66)", r.json().get("id") == "rem-66"
              and r.json()["focus"]["reference"] == "ServiceRequest/rem-66", json.dumps(r.json())[:300])
        r2 = c.post(f"{BASE}/interop/Bundle", json=ejemplo, headers={**api_key, "x-nonce": secrets.token_urlsafe(18)})
        check("La misma remisión otra vez → 409 duplicate", r2.status_code == 409, r2.text)
        r3 = c.post(f"{BASE}/interop/Bundle", json=ejemplo, headers=api_key)
        check("El mismo x-nonce otra vez → 409", r3.status_code == 409 and "nonce" in r3.text.lower(), r3.text)
        r4 = c.get(f"{BASE}{loc}", headers={"Authorization": f"Bearer {token}"})
        check("GET /interop/Task/{id} → estado requested", r4.status_code == 200 and r4.json()["status"] == "requested", r4.text)
        refs = c.get(f"{BASE}/referrals", headers=H["admin"]).json()
        rx = next((x for x in refs if x.get("origen") == "externo" and x.get("institucion") == socio and "torácico" in x["motivo"]), None)
        check("Quedó como remisión externa con prioridad, observaciones y reporte aprobado", bool(rx) and rx["prioridad"] == "stat"
              and rx["observaciones"] == 3 and "APROBADO" in (rx.get("reporte_ia") or ""), json.dumps(rx)[:300])
        det = c.get(f"{BASE}/remisiones/externas", headers=H["admin"]).json()
        guardado = next((x for x in det if x["institucion"] == socio and x["direccion"] == "entrante"), None)
        if guardado:
            b = c.get(f"{BASE}/remisiones/externas/{guardado['id']}", headers=H["admin"]).json().get("bundle") or {}
            pac = next(e["resource"] for e in b.get("entry", []) if e["resource"]["resourceType"] == "Patient")
            check("No se guardan teléfono, correo ni dirección del Patient", not any(k in pac for k in ("telecom", "address")), json.dumps(pac)[:200])

    print("\n### 4. Viaje completo por nuestra propia puerta (consentimiento → envío → respuesta)")
    pacientes = c.get(f"{BASE}/patients", headers=H["medico"]).json()
    if not pacientes:
        check("El médico tiene al menos un paciente (corra scripts/seed_demo_api.py)", False)
        return resumen(c, H, socio)
    pac = pacientes[0]
    for x in c.get(f"{BASE}/patients/{pac['id']}/consentimientos", headers=H["medico"]).json():
        if x["institucion"] == socio and x["estado"] == "activo":
            c.delete(f"{BASE}/patients/{pac['id']}/consentimientos/{x['id']}", headers=H["medico"])
    cuerpo = {"paciente_id": pac["id"], "institucion": socio, "motivo": f"Prueba {TAG}: FEVI baja y riesgo alto; valoración", "prioridad": "urgent"}
    r = c.post(f"{BASE}/remisiones/externas", headers=H["medico"], json=cuerpo)
    check("Sin consentimiento para ESA institución → 409 y no sale nada", r.status_code == 409 and "consentimiento" in r.text.lower(), r.text)
    r = c.post(f"{BASE}/patients/{pac['id']}/consentimientos", headers=H["medico"], json={"institucion": socio, "nota": "Autorización verbal registrada"})
    check("Registrar consentimiento (Ley 1581)", r.status_code == 201 and r.json()["estado"] == "activo", r.text)
    cons_id = r.json()["id"]
    r = c.get(f"{BASE}/patients/{pac['id']}/consentimientos?formato=fhir", headers=H["medico"])
    check("El consentimiento sale como recurso FHIR Consent", r.status_code == 200 and r.json()["entry"][0]["resource"]["resourceType"] == "Consent", r.text[:200])

    aprobado = any(x.get("estado") in ("approved", "aprobado") for x in c.get(f"{BASE}/patients/{pac['id']}/ai/reports", headers=H["medico"]).json())
    if not aprobado:
        r = c.post(f"{BASE}/patients/{pac['id']}/ai/analyze", headers=H["medico"], json={
            "age": 72, "anaemia": 1, "creatinine_phosphokinase": 250, "diabetes": 1, "ejection_fraction": 20, "high_blood_pressure": 1,
            "platelets": 250000, "serum_creatinine": 2.3, "serum_sodium": 126, "sex": 1, "smoking": 0, "time": 30})
        if r.status_code == 201:
            c.post(f"{BASE}/ai/reports/{r.json()['id']}/approve", headers=H["medico"], json={"observaciones": "Coincide con la clínica"})
            aprobado = any(x.get("estado") in ("approved", "aprobado") for x in c.get(f"{BASE}/patients/{pac['id']}/ai/reports", headers=H["medico"]).json())
    check("El paciente tiene un reporte de IA APROBADO (requisito para remitir)", aprobado)

    r = c.post(f"{BASE}/remisiones/externas", headers=H["medico"], json=cuerpo)
    ok = check("Remitir → Bundle enviado y aceptado por el socio (201)", r.status_code == 201 and r.json().get("estado_envio") == "enviada"
               and r.json().get("envio_http") == 201, r.text)
    if ok:
        saliente = r.json()
        det = c.get(f"{BASE}/remisiones/externas/{saliente['id']}", headers=H["medico"]).json()
        tipos = [e["resource"]["resourceType"] for e in det["bundle"]["entry"]]
        check("El Bundle lleva Patient, ServiceRequest, Observation, DiagnosticReport, Consent y Task", all(t in tipos for t in
              ("Patient", "ServiceRequest", "Observation", "DiagnosticReport", "Consent", "Task")), str(tipos))
        pac_fhir = next(e["resource"] for e in det["bundle"]["entry"] if e["resource"]["resourceType"] == "Patient")
        check("Minimización: el Patient no lleva teléfono, correo ni dirección", not any(k in pac_fhir for k in ("telecom", "address")), json.dumps(pac_fhir))
        obs = [e["resource"] for e in det["bundle"]["entry"] if e["resource"]["resourceType"] == "Observation"]
        check("Observations con LOINC y UCUM", obs and all(o["code"]["coding"][0]["system"] == "http://loinc.org" for o in obs)
              and all(o.get("valueQuantity", {}).get("system", "http://unitsofmeasure.org") == "http://unitsofmeasure.org" for o in obs), json.dumps(obs)[:300])

        entrante = buscar_entrante(c, H, socio, TAG)
        check("Del otro lado llegó como remisión pendiente para un especialista", bool(entrante), "")
        if entrante:
            pend = c.get(f"{BASE}/pending", headers=H["especialista"]).json().get("remisiones_recibidas", 0)
            quien = H["especialista"] if entrante["_de_especialista"] else H["admin"]
            if entrante["_de_especialista"]:
                check("El contador de remisiones del especialista la incluye", pend >= 1, str(pend))
            r = c.post(f"{BASE}/referrals/{entrante['id']}/reject", headers=quien, json={"motivo": "Requiere primero ecocardiograma reciente"})
            check("El especialista rechaza con motivo → el Task vuelve solo al socio (2xx)", r.status_code == 200
                  and (r.json().get("respuesta_socio") or {}).get("envio") == "enviada", r.text)
            r = c.get(f"{BASE}/remisiones/externas", headers=H["medico"]).json()
            mia = next((x for x in r if x["id"] == saliente["id"]), {})
            check("El médico que remitió ve el rechazo y el motivo", mia.get("estado") == "rejected" and "ecocardiograma" in (mia.get("respuesta_motivo") or ""), json.dumps(mia)[:300])

        r = c.post(f"{BASE}/remisiones/externas", headers=H["medico"], json={**cuerpo, "motivo": f"Prueba {TAG}-b: segunda remisión"})
        entrante = buscar_entrante(c, H, socio, f"{TAG}-b") if r.status_code == 201 else None
        if entrante:
            quien = H["especialista"] if entrante["_de_especialista"] else H["admin"]
            r = c.post(f"{BASE}/referrals/{entrante['id']}/accept", headers=quien, json={"nota": "La valoramos mañana a las 8:00"})
            check("Aceptar → Task accepted entregado", r.status_code == 200 and (r.json().get("respuesta_socio") or {}).get("envio") == "enviada", r.text)
            mia = next((x for x in c.get(f"{BASE}/remisiones/externas", headers=H["medico"]).json() if f"{TAG}-b" in x["motivo"] and x["direccion"] == "saliente"), {})
            check("El médico ve la aceptación", mia.get("estado") == "accepted", json.dumps(mia)[:300])
            r = c.post(f"{BASE}/interop/Task", headers=T_task, json={"resourceType": "Task", "status": "accepted", "intent": "order",
                                                                    "focus": {"reference": f"ServiceRequest/rem-{mia.get('numero')}"}, "statusReason": {"text": "La valoramos mañana a las 8:00"}})
            check("El mismo Task otra vez (reintento) → 200 sin duplicar el aviso", r.status_code == 200, r.text)
            r = c.post(f"{BASE}/interop/Task", headers=T_task, json={"resourceType": "Task", "status": "accepted", "focus": {"reference": "ServiceRequest/rem-999999999"}})
            check("Task sobre una remisión que no le enviamos → 404", r.status_code == 404, r.text)
            r = c.post(f"{BASE}/interop/Task", headers=T_task, json={"resourceType": "Task", "status": "aprobado", "focus": {"reference": f"ServiceRequest/rem-{mia.get('numero')}"}})
            check("Task con estado desconocido → 422", r.status_code == 422, r.text)

    r = c.delete(f"{BASE}/patients/{pac['id']}/consentimientos/{cons_id}", headers=H["medico"])
    check("Revocar el consentimiento", r.status_code == 200 and r.json()["estado"] == "revocado", r.text)
    r = c.post(f"{BASE}/remisiones/externas", headers=H["medico"], json=cuerpo)
    check("Revocado → ya no se puede remitir (409)", r.status_code == 409, r.text)

    print("\n### 5. Firma HMAC opcional por institución")
    firmado = f"firmado-{TAG}"
    r = c.post(f"{BASE}/interop/socios", headers=H["admin"], json={"codigo": firmado, "nombre": f"Firmado {TAG}", "url_base": URL_INTERNA, "con_firma": True})
    fc = r.json()["credenciales_una_sola_vez"]
    tok = c.post(f"{BASE}/oauth/token", data={"grant_type": "client_credentials", "client_id": fc["client_id"], "client_secret": fc["client_secret"]}).json()["access_token"]
    cuerpo_b = json.dumps({"resourceType": "Task", "status": "accepted", "focus": {"reference": "ServiceRequest/rem-1"}}, separators=(",", ":")).encode()

    def firmar(ts, nonce, cuerpo_f):
        cadena = "\n".join(["v1", ts, nonce, "POST", "/interop/Task", hashlib.sha256(cuerpo_f).hexdigest()])
        return "v1=" + hmac.new(fc["firma_secreto"].encode(), cadena.encode(), hashlib.sha256).hexdigest()

    base_h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/fhir+json"}
    r = c.post(f"{BASE}/interop/Task", content=cuerpo_b, headers=base_h)
    check("Institución con firma acordada: sin firma → 401", r.status_code == 401, r.text)
    ts, nonce = str(int(time.time())), secrets.token_urlsafe(18)
    r = c.post(f"{BASE}/interop/Task", content=cuerpo_b.replace(b"accepted", b"rejected"), headers={**base_h, "X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": firmar(ts, nonce, cuerpo_b)})
    check("Cuerpo cambiado después de firmar → 401 firma inválida", r.status_code == 401 and "Firma" in r.text, r.text)
    ts_viejo, nonce = str(int(time.time()) - 3600), secrets.token_urlsafe(18)
    r = c.post(f"{BASE}/interop/Task", content=cuerpo_b, headers={**base_h, "X-Timestamp": ts_viejo, "X-Nonce": nonce, "X-Signature": firmar(ts_viejo, nonce, cuerpo_b)})
    check("Firma con una hora de atraso → 401", r.status_code == 401 and "ventana" in r.text, r.text)
    ts, nonce = str(int(time.time())), secrets.token_urlsafe(18)
    hdr = {**base_h, "X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": firmar(ts, nonce, cuerpo_b)}
    r = c.post(f"{BASE}/interop/Task", content=cuerpo_b, headers=hdr)
    check("Firma válida pasa la puerta (404: esa remisión no es suya)", r.status_code == 404, r.text)
    r = c.post(f"{BASE}/interop/Task", content=cuerpo_b, headers=hdr)
    check("Replay exacto (mismo nonce) → 409", r.status_code == 409, r.text)

    print("\n### 6. Desactivar corta el acceso aunque el token no haya vencido")
    for cod in (socio, firmado):
        c.put(f"{BASE}/interop/socios/{cod}", headers=H["admin"], json={"estado": "inactivo"})
    r = c.post(f"{BASE}/interop/Bundle", json=ejemplo, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/fhir+json"})
    check("Institución desactivada → 401 con su token aún vigente", r.status_code == 401, r.text)
    return resumen(c, H, socio)


def buscar_entrante(c, H, socio, marca):
    for _ in range(10):
        for quien, es_esp in ((H["especialista"], True), (H["admin"], False)):
            for x in c.get(f"{BASE}/referrals", headers=quien).json():
                if x.get("origen") == "externo" and x.get("institucion") == socio and marca in x["motivo"] and x["estado"] == "pendiente":
                    return {**x, "_de_especialista": es_esp}
        time.sleep(0.5)
    return None


def resumen(c, H, socio) -> int:
    print("\n### 7. Auditoría con origen externo")
    r = c.get(f"{BASE}/audit/logs?limit=500", headers=H["admin"])
    acciones = {x.get("action") for x in r.json()} if r.status_code == 200 else set()
    esperadas = {"INTEROP_TOKEN_EMITIDO", "INTEROP_TOKEN_RECHAZADO", "INTEROP_RECHAZO", "INTEROP_REMISION_RECIBIDA",
                 "INTEROP_REMISION_ENVIADA", "INTEROP_RESPUESTA_ENVIADA", "INTEROP_RESPUESTA_RECIBIDA", "CONSENTIMIENTO_REGISTRADO"}
    check("La auditoría registra tokens, rechazos, envíos, recepciones y respuestas", esperadas <= acciones, str(sorted(esperadas - acciones)))
    ok = sum(1 for x, _ in resultados if x)
    print(f"\nResultado: {ok}/{len(resultados)}")
    for x, nombre in resultados:
        if not x:
            print(f"  · falló: {nombre}")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
