import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ImagingStudy(Base):
    __tablename__ = "imaging_studies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    dataset: Mapped[str] = mapped_column(String(100), nullable=False, server_default="CAMUS")
    source_subject_id: Mapped[str] = mapped_column(String(100), nullable=False)
    modality: Mapped[str] = mapped_column(String(20), nullable=False, server_default="US")
    view: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phase: Mapped[str | None] = mapped_column(String(20), nullable=True)
    orthanc_instance_id: Mapped[str] = mapped_column(String(120), nullable=False)
    academic_link: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    source_license: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
