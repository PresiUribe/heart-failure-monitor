from datetime import datetime, timezone
from functools import lru_cache
from pymongo import MongoClient
from app.config import settings


@lru_cache(maxsize=1)
def mongo_client():
    return MongoClient(settings.mongo_url, serverSelectionTimeoutMS=4000)


def billing_collection():
    col = mongo_client()[settings.mongo_database]["invoices"]
    col.create_index("paciente_id")
    col.create_index("reporte_id")
    return col


def create_invoice(*, patient_id: str, service: str, value: float, created_by: str, reporte_id: str | None = None):
    doc = {
        # `paciente_id` es el campo exigido por el contrato del tester;
        # `patient_id` se conserva por compatibilidad con documentos previos.
        "paciente_id": patient_id,
        "patient_id": patient_id,
        "servicio": service,
        "valor": float(value),
        "fecha": datetime.now(timezone.utc),
        "estado_pago": "PENDIENTE",
        "created_by": created_by,
    }
    if reporte_id:
        doc["reporte_id"] = reporte_id
    result = billing_collection().insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc
