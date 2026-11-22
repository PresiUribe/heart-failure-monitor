import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.database import Base
from app.security.campos import Cifrado, indice_ciego


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (
        CheckConstraint(
            "sex IS NULL OR sex IN ('MALE', 'FEMALE', 'OTHER', 'UNKNOWN')",
            name="chk_patient_sex",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    patient_code: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
        server_default=text("('PAT-' || lpad(nextval('patient_code_seq')::text, 6, '0'))"),
    )
    document_type: Mapped[str] = mapped_column(String(10), nullable=False, server_default="CC")
    # R27: cifrados con AES-256-GCM en la base; la aplicación los ve en claro.
    document_number: Mapped[str] = mapped_column(Cifrado("patients.document_number"), nullable=False)
    document_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)  # índice ciego (búsqueda/unicidad)
    phone: Mapped[str | None] = mapped_column(Cifrado("patients.phone"), nullable=True)
    address: Mapped[str | None] = mapped_column(Cifrado("patients.address"), nullable=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[str | None] = mapped_column(String(20), nullable=True)
    municipality: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rural_area: Mapped[str | None] = mapped_column(Cifrado("patients.rural_area"), nullable=True)  # vereda: parte de la dirección
    primary_diagnosis: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fhir_patient_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Interoperabilidad (005): seudónimo federado entre instituciones y de dónde llegó el paciente
    sid_federado: Mapped[str | None] = mapped_column(String(32), nullable=True)
    origen_institucion: Mapped[str | None] = mapped_column(String(60), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    @validates("document_number")
    def _indexar_documento(self, _key, valor):
        self.document_hash = indice_ciego(valor)
        return valor
