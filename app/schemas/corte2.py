import uuid
from datetime import datetime
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class MeResponse(BaseModel):
    id: uuid.UUID
    user_code: str
    document_type: str
    document_number: str
    username: str
    rol: str


class ReferralCreate(BaseModel):
    # Acepta nombres en inglés (frontend) y en español (tester/plantilla).
    model_config = ConfigDict(populate_by_name=True)
    specialist_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("specialist_id", "especialista_id", "especialista", "specialist", "destinatario_id"),
        description='Documento, username, user_code o UUID del especialista. Si se omite se usa el primer especialista activo.',
    )
    reason: str = Field(
        default="Valoración por especialista",
        min_length=3,
        max_length=1000,
        validation_alias=AliasChoices("reason", "motivo"),
    )


class AIAnalyzeRequest(BaseModel):
    age: float = Field(ge=18, le=110)
    anaemia: int = Field(ge=0, le=1)
    creatinine_phosphokinase: float = Field(ge=0)
    diabetes: int = Field(ge=0, le=1)
    ejection_fraction: float = Field(ge=1, le=90)
    high_blood_pressure: int = Field(ge=0, le=1)
    platelets: float = Field(ge=1000)
    serum_creatinine: float = Field(ge=0)
    serum_sodium: float = Field(ge=90, le=180)
    sex: int = Field(ge=0, le=1)
    smoking: int = Field(ge=0, le=1)
    time: int = Field(default=30, ge=1, le=365)
    image_id: uuid.UUID | None = None


class AIReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    observations: str | None = Field(
        default=None,
        max_length=2000,
        validation_alias=AliasChoices("observations", "observaciones", "observacion", "nota", "note", "notes"),
    )


class AlertAcknowledgeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    note: str = Field(
        default="Alerta reconocida por el clínico responsable.",
        min_length=3,
        max_length=1000,
        validation_alias=AliasChoices("note", "nota", "observaciones", "observacion", "notes"),
    )


class InvoiceCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    patient_id: str = Field(
        validation_alias=AliasChoices("patient_id", "paciente_id", "paciente"),
        description='Documento del paciente (preferido), patient_code o UUID interno',
    )
    service: str = Field(
        default="Consulta médica",
        min_length=3,
        max_length=150,
        validation_alias=AliasChoices("service", "servicio", "concepto"),
    )
    value: float = Field(gt=0, validation_alias=AliasChoices("value", "valor", "monto"))


class FHIRHookEvent(BaseModel):
    resourceType: str | None = None
