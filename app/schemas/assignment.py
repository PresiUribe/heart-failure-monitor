import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RelationshipType = Literal['PACIENTE', 'CUIDADOR', 'MEDICO_RURAL', 'ESPECIALISTA']


class AssignmentCreate(BaseModel):
    user_id: str = Field(description='Documento del usuario (preferido), user_code o UUID interno')
    relationship_type: RelationshipType
    is_primary: bool = False


class AssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    patient_id: uuid.UUID
    user_id: uuid.UUID
    relationship_type: str
    is_primary: bool
    assigned_at: datetime
    active: bool
