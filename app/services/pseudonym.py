import hashlib
from app.config import settings


def patient_pseudonym(patient_id: str) -> str:
    raw = f"{settings.jwt_secret_key}:{patient_id}".encode("utf-8")
    return "PSN-" + hashlib.sha256(raw).hexdigest()[:12].upper()
