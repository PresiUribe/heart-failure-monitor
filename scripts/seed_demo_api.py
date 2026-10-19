"""Create the five academic test roles and one linked heart-failure patient.

Run after `docker compose up -d`:
    python scripts/seed_demo_api.py

The credentials are intentionally DEMO credentials and are also declared in
`tester.yaml`; they are not production secrets.
"""
import sys
import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")

ACCOUNTS = [
    ("admin.demo", "AdminDemo2026!", "admin.demo@hfmonitor.co", "Administrador Demo", "ADMIN", "1000000001"),
    ("medico.demo", "MedicoDemo2026!", "medico.demo@hfmonitor.co", "Médico Demo", "MEDICO", "1000000002"),
    ("especialista.demo", "EspecialistaDemo2026!", "especialista.demo@hfmonitor.co", "Especialista Cardiología Demo", "ESPECIALISTA", "1000000003"),
    ("paciente.demo", "PacienteDemo2026!", "paciente.demo@hfmonitor.co", "Paciente Demo", "PACIENTE", "1000000004"),
    ("contable.demo", "ContableDemo2026!", "contable.demo@hfmonitor.co", "Contable Demo", "CONTABLE", "1000000005"),
]


def login(client, username, password):
    r = client.post(f"{BASE}/auth/login", data={"username": username, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


def main():
    with httpx.Client(timeout=30) as client:
        # Bootstrap only if the database is empty.
        r = client.post(
            f"{BASE}/setup/bootstrap-admin",
            json={"document_type":"CC", "document_number":ACCOUNTS[0][5], "full_name": ACCOUNTS[0][3], "username": ACCOUNTS[0][0], "email": ACCOUNTS[0][2], "password": ACCOUNTS[0][1]},
        )
        if r.status_code not in (201, 409):
            r.raise_for_status()

        token = login(client, ACCOUNTS[0][0], ACCOUNTS[0][1])
        headers = {"Authorization": f"Bearer {token}"}

        existing = client.get(f"{BASE}/users", headers=headers).json()
        by_username = {u["username"]: u for u in existing}
        # Si la base viene del Corte 1/una versión previa, la migración 004 crea
        # identificadores LEGACY. Para las cuentas demo los sustituimos por los
        # documentos académicos reproducibles definidos arriba.
        for username, _password, _email, _full_name, _role, document_number in ACCOUNTS:
            current = by_username.get(username)
            if current and current.get("document_number") != document_number:
                rr = client.put(
                    f"{BASE}/users/{current['id']}",
                    headers=headers,
                    json={"document_type": "CC", "document_number": document_number},
                )
                rr.raise_for_status()
                by_username[username] = rr.json()
        for username, password, email, full_name, role, document_number in ACCOUNTS[1:]:
            if username not in by_username:
                rr = client.post(
                    f"{BASE}/users",
                    headers=headers,
                    json={"document_type":"CC", "document_number":document_number, "full_name": full_name, "username": username, "email": email, "password": password, "role_code": role},
                )
                rr.raise_for_status()
                by_username[username] = rr.json()

        patients = client.get(f"{BASE}/patients", headers=headers).json()
        patient = next((p for p in patients if p["document_number"] == "DEMO-HF-001"), None)
        if not patient:
            rr = client.post(
                f"{BASE}/patients",
                headers=headers,
                json={
                    "document_number": "DEMO-HF-001",
                    "first_name": "Paciente",
                    "last_name": "Académico Cardiaco",
                    "birth_date": "1954-05-12",
                    "sex": "MALE",
                    "municipality": "Cali",
                    "rural_area": "Caso académico rural",
                    "primary_diagnosis": "I50.9 - Insuficiencia cardiaca",
                },
            )
            rr.raise_for_status()
            patient = rr.json()

        assignments = [
            ("medico.demo", "MEDICO_RURAL", True),
            ("especialista.demo", "ESPECIALISTA", False),
            ("paciente.demo", "PACIENTE", True),
        ]
        for username, relationship, primary in assignments:
            rr = client.post(
                f"{BASE}/patients/{patient['document_number']}/assignments",
                headers=headers,
                json={"user_id": by_username[username]["document_number"], "relationship_type": relationship, "is_primary": primary},
            )
            if rr.status_code not in (201, 409):
                rr.raise_for_status()

        # Best effort FHIR sync so R15 has a mapped Patient ready.
        fr = client.post(f"{BASE}/fhir/patients/{patient['document_number']}/sync", headers=headers)
        print("FHIR sync:", fr.status_code, fr.text[:200])
        print("\nDemo ready")
        print("Patient:", patient["patient_code"], patient["id"])
        for username, password, *_ in ACCOUNTS:
            print(f"  {username:20s} {password}")


if __name__ == "__main__":
    main()
