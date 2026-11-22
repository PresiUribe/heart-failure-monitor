"""Manda a la papelera (soft delete) los datos que crean los scripts de prueba, para dejar la demo limpia.

Por defecto solo MUESTRA lo que haría. Para aplicarlo:
    docker compose exec api python scripts/limpiar_pruebas.py            # vista previa
    docker compose exec api python scripts/limpiar_pruebas.py --aplicar  # aplicar

Qué toca (y nada más):
- Pacientes creados por las pruebas: CANARIO… Prueba (e2e_check), Prueba Cifrado (test_seguridad), Canarito Zuluaga…
  (test_agente_seguridad), Canaria Zuluaga Pérez y Otoniel Bermúdez Rey (agente_check), y los que llegaron desde
  instituciones de prueba (prueba-xxxxxx / firmado-xxxxxx, p. ej. Luis Saliente). Van a la papelera: el admin los
  puede restaurar.
- Remisiones PENDIENTES de esos pacientes o que vinieron de instituciones de prueba: se cierran como rechazadas
  ("datos de prueba") para que no inflen el contador del especialista.
- Alertas activas y reportes de IA pendientes de esos pacientes: se cierran.
Nunca toca al paciente demo (DEMO-HF-001), ni a Pulso u otros socios reales, ni la auditoría (que es de solo agregar).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.ai_report import AIReport  # noqa: E402
from app.models.alert import Alert  # noqa: E402
from app.models.interop import RemisionExterna, Socio  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.models.referral import Referral  # noqa: E402
from app.models.role import Role  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.audit import add_audit, add_history, model_snapshot  # noqa: E402

SOCIO_PRUEBA = re.compile(r"^(prueba|firmado)-[0-9a-f]{6}$")
PROTEGIDOS = {"DEMO-HF-001"}


def es_de_prueba(p: Patient) -> str | None:
    nombre, apellido = (p.first_name or ""), (p.last_name or "")
    if p.document_number in PROTEGIDOS:
        return None
    if nombre.startswith("CANARIO") and apellido == "Prueba":
        return "e2e_check"
    if nombre == "Prueba" and apellido.startswith("Cifrado"):
        return "test_seguridad"
    if nombre == "Canarito" and apellido.startswith("Zuluaga"):
        return "test_agente_seguridad"
    if (nombre, apellido) in {("Canaria", "Zuluaga Pérez"), ("Otoniel", "Bermúdez Rey")}:
        return "agente_check"
    if p.origen_institucion and SOCIO_PRUEBA.match(p.origen_institucion):
        return f"institución de prueba {p.origen_institucion}"
    return None


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    with SessionLocal() as db:
        admin = db.scalar(select(User).join(Role, Role.id == User.role_id)
                          .where(Role.code == "ADMIN", User.is_active.is_(True)).order_by(User.created_at))
        if admin is None:
            sys.exit("No hay un administrador activo para firmar la limpieza en la auditoría.")
        pacientes = [(p, es_de_prueba(p)) for p in db.scalars(select(Patient).where(Patient.deleted_at.is_(None)))]
        pacientes = [(p, motivo) for p, motivo in pacientes if motivo]
        ids = {p.id for p, _ in pacientes}
        socios_prueba = {s.id for s in db.scalars(select(Socio)) if SOCIO_PRUEBA.match(s.codigo)}
        refs_socio = {rx.referral_id for rx in db.scalars(select(RemisionExterna).where(RemisionExterna.socio_id.in_(socios_prueba)))
                      if rx.referral_id} if socios_prueba else set()
        remisiones = [r for r in db.scalars(select(Referral).where(Referral.status == "PENDING"))
                      if r.patient_id in ids or r.id in refs_socio]
        alertas = list(db.scalars(select(Alert).where(Alert.state == "ACTIVE", Alert.patient_id.in_(ids)))) if ids else []
        reportes = list(db.scalars(select(AIReport).where(AIReport.status == "PENDING", AIReport.patient_id.in_(ids)))) if ids else []

        print(f"Pacientes de prueba a la papelera: {len(pacientes)}")
        for p, motivo in pacientes:
            print(f"  · {p.patient_code} · {p.first_name} {p.last_name}  ({motivo})")
        print(f"Remisiones pendientes que se cierran: {len(remisiones)}")
        print(f"Alertas activas que se cierran: {len(alertas)}")
        print(f"Reportes de IA pendientes que se cierran: {len(reportes)}")
        if not aplicar:
            print("\nVista previa: no se cambió nada. Para aplicar:  python scripts/limpiar_pruebas.py --aplicar")
            return 0

        ahora = datetime.now(timezone.utc).replace(tzinfo=None)
        for r in remisiones:
            r.status, r.response_note, r.responded_at = "REJECTED", "Cerrada: datos de prueba", ahora
        for a in alertas:
            a.state, a.acknowledged_by, a.acknowledged_at = "ACKNOWLEDGED", admin.id, ahora
            a.acknowledgment_note = "Cerrada: datos de prueba"
        for rep in reportes:
            rep.status = "REJECTED"
            rep.clinical_notes = "Cerrado: datos de prueba"
        for p, motivo in pacientes:
            previo = model_snapshot(p)
            p.is_active, p.deleted_at, p.deleted_by, p.updated_at = False, ahora, admin.id, ahora
            add_history(db, entity_type="PATIENT", entity_id=p.id, previous_data=previo, new_data=model_snapshot(p),
                        changed_by=admin.id, change_type="SOFT_DELETE")
            add_audit(db, user_id=admin.id, action="SOFT_DELETE", entity_type="PATIENT", entity_id=p.id,
                      details={"motivo": f"limpieza de datos de prueba ({motivo})"})
        add_audit(db, user_id=admin.id, action="LIMPIEZA_DATOS_PRUEBA", entity_type="SYSTEM", entity_id=None,
                  details={"pacientes": len(pacientes), "remisiones": len(remisiones), "alertas": len(alertas),
                           "reportes": len(reportes)})
        db.commit()
    print("\nListo. Los pacientes quedaron en la papelera (el admin los puede restaurar).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
