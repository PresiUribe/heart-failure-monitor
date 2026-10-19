import uuid
from datetime import date, datetime
from typing import Literal

import secrets

from pydantic import BaseModel, ConfigDict, Field, model_validator


SexCode = Literal["MALE", "FEMALE", "OTHER", "UNKNOWN"]


_SEX_MAP = {
    "m": "MALE", "male": "MALE", "masculino": "MALE", "hombre": "MALE", "1": "MALE",
    "f": "FEMALE", "female": "FEMALE", "femenino": "FEMALE", "mujer": "FEMALE", "0": "FEMALE",
    "other": "OTHER", "otro": "OTHER", "unknown": "UNKNOWN", "desconocido": "UNKNOWN",
}


class PatientCreate(BaseModel):
    """Alta de paciente.

    Acepta los nombres del Corte 1 (inglés) y nombres en español usados por
    el tester (documento, nombre, apellido...). Correo, teléfono y dirección
    se aceptan pero NO se persisten ni se envían a la IA (minimización, Ley 1581).
    """

    @model_validator(mode="before")
    @classmethod
    def normalize_spanish_keys(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        aliases = {
            "document_number": ["documento", "numero_documento", "document", "identificacion", "cedula", "identifier"],
            "first_name": ["nombres", "primer_nombre", "given", "nombre_pila"],
            "last_name": ["apellidos", "apellido", "family"],
            "birth_date": ["fecha_nacimiento", "nacimiento", "birthDate"],
            "sex": ["sexo", "genero", "gender"],
            "municipality": ["municipio", "ciudad", "city"],
            "rural_area": ["vereda", "zona_rural"],
            "primary_diagnosis": ["diagnostico", "diagnostico_principal"],
        }
        for target, keys in aliases.items():
            if d.get(target) in (None, ""):
                for k in keys:
                    if d.get(k) not in (None, ""):
                        d[target] = d[k]
                        break
        full = d.get("nombre") or d.get("name") or d.get("nombre_completo") or d.get("full_name")
        if isinstance(full, str) and full.strip() and (not d.get("first_name") or not d.get("last_name")):
            parts = full.strip().split()
            d.setdefault("first_name", parts[0])
            if not d.get("first_name"):
                d["first_name"] = parts[0]
            if not d.get("last_name"):
                d["last_name"] = " ".join(parts[1:]) or parts[0]
        if not d.get("document_number"):
            d["document_number"] = "AUTO-" + secrets.token_hex(5).upper()
        d["document_number"] = str(d["document_number"])
        if d.get("sex") is not None:
            d["sex"] = _SEX_MAP.get(str(d["sex"]).strip().lower(), str(d["sex"]).upper())
            if d["sex"] not in {"MALE", "FEMALE", "OTHER", "UNKNOWN"}:
                d["sex"] = "UNKNOWN"
        allowed = {"document_number", "first_name", "last_name", "birth_date", "sex", "municipality", "rural_area", "primary_diagnosis"}
        return {k: v for k, v in d.items() if k in allowed}

    document_number: str = Field(min_length=3, max_length=50)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    birth_date: date | None = None
    sex: SexCode | None = None
    municipality: str | None = Field(default=None, max_length=120)
    rural_area: str | None = Field(default=None, max_length=150)
    primary_diagnosis: str | None = Field(default=None, max_length=255)


class PatientUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    birth_date: date | None = None
    sex: SexCode | None = None
    municipality: str | None = Field(default=None, max_length=120)
    rural_area: str | None = Field(default=None, max_length=150)
    primary_diagnosis: str | None = Field(default=None, max_length=255)


class PatientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_code: str
    document_number: str
    first_name: str
    last_name: str
    birth_date: date | None
    sex: str | None
    municipality: str | None
    rural_area: str | None
    primary_diagnosis: str | None
    fhir_patient_id: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
