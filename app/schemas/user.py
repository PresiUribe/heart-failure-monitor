import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

RoleCode = Literal[
    'ADMIN', 'MEDICO', 'MEDICO_RURAL', 'ESPECIALISTA',
    'PACIENTE', 'PACIENTE_CUIDADOR', 'CONTABLE',
]
DocumentType = Literal['CC', 'CE', 'TI', 'PA', 'PPT', 'RC']


class BootstrapAdminRequest(BaseModel):
    document_type: DocumentType = 'CC'
    document_number: str = Field(min_length=3, max_length=50, description='Numero de identificacion del usuario')
    full_name: str | None = Field(default=None, max_length=150)
    username: str = Field(min_length=3, max_length=100, description='Usuario usado para iniciar sesion')
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserCreate(BaseModel):
    document_type: DocumentType = 'CC'
    document_number: str = Field(min_length=3, max_length=50, description='Numero de identificacion del usuario')
    full_name: str | None = Field(default=None, max_length=150)
    username: str = Field(min_length=3, max_length=100, description='Usuario usado para iniciar sesion')
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role_code: RoleCode


class UserUpdate(BaseModel):
    document_type: DocumentType | None = None
    document_number: str | None = Field(default=None, min_length=3, max_length=50)
    full_name: str | None = Field(default=None, max_length=150)
    email: EmailStr | None = None
    role_code: RoleCode | None = None
    is_active: bool | None = None


class AdminPasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class PasswordResetResponse(BaseModel):
    user_id: uuid.UUID
    document_number: str
    user_code: str
    username: str
    message: str


class RoleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_code: str
    document_type: str
    document_number: str
    full_name: str | None
    username: str
    email: EmailStr
    is_active: bool
    failed_login_attempts: int = 0
    is_locked: bool = False
    locked_at: datetime | None = None
    created_at: datetime
    deleted_at: datetime | None
    role: RoleSummary
