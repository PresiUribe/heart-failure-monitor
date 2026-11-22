"""Tablas de interoperabilidad entre instituciones (sql/migrations/005_interoperabilidad.sql)."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, FetchedValue, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

SCOPES_POR_DEFECTO = "system/ServiceRequest.write system/Task.write"


class Socio(Base):
    __tablename__ = "interop_socios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    url_base: Mapped[str | None] = mapped_column(String(300))
    estado: Mapped[str] = mapped_column(String(20), nullable=False, server_default="activo")
    es_espejo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Entrada: la credencial que les dimos (el secreto solo como hash Argon2id)
    client_id: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    secreto_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[str] = mapped_column(String(200), nullable=False, server_default=SCOPES_POR_DEFECTO)
    exige_firma: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    firma_secreto_cif: Mapped[str | None] = mapped_column(Text)
    # Salida: la credencial que nos dieron (cifrada con AES-256-GCM)
    auth_salida: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ninguno")
    ruta_token: Mapped[str] = mapped_column(String(200), nullable=False, server_default="/oauth/token")
    ruta_bundle: Mapped[str] = mapped_column(String(200), nullable=False, server_default="/interop/Bundle")
    ruta_task: Mapped[str] = mapped_column(String(200), nullable=False, server_default="/interop/Task")
    cabecera_api_key: Mapped[str] = mapped_column(String(80), nullable=False, server_default="X-API-Key")
    scope_salida: Mapped[str | None] = mapped_column(String(200))
    cred_id_salida: Mapped[str | None] = mapped_column(String(200))
    cred_secreto_salida_cif: Mapped[str | None] = mapped_column(Text)
    firma_salida: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    firma_secreto_salida_cif: Mapped[str | None] = mapped_column(Text)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actualizado: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Consentimiento(Base):
    __tablename__ = "consentimientos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    socio_id: Mapped[int] = mapped_column(Integer, ForeignKey("interop_socios.id"), nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, server_default="activo")
    finalidad: Mapped[str] = mapped_column(String(20), nullable=False, server_default="TREAT")
    nota: Mapped[str | None] = mapped_column(Text)
    registrado_por: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    registrado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    revocado_por: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    revocado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RemisionExterna(Base):
    __tablename__ = "remisiones_externas"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    numero: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False,
                                        server_default=FetchedValue())
    direccion: Mapped[str] = mapped_column(String(10), nullable=False)
    socio_id: Mapped[int] = mapped_column(Integer, ForeignKey("interop_socios.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    referral_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("referrals.id"))
    medico_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    estado: Mapped[str] = mapped_column(String(20), nullable=False, server_default="requested")
    prioridad: Mapped[str] = mapped_column(String(10), nullable=False, server_default="routine")
    especialidad: Mapped[str | None] = mapped_column(String(120))
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    clave_externa: Mapped[str | None] = mapped_column(String(300))
    sr_id_externo: Mapped[str | None] = mapped_column(String(200))
    sr_identifier: Mapped[dict | None] = mapped_column(JSONB)
    task_id_externo: Mapped[str | None] = mapped_column(String(200))
    patient_ref_externo: Mapped[str | None] = mapped_column(String(200))
    reporte: Mapped[dict | None] = mapped_column(JSONB)
    consentimiento: Mapped[dict | None] = mapped_column(JSONB)
    observaciones: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    bundle: Mapped[dict | None] = mapped_column(JSONB)
    estado_envio: Mapped[str | None] = mapped_column(String(20))
    envio_http: Mapped[int | None] = mapped_column(Integer)
    envio_detalle: Mapped[str | None] = mapped_column(Text)
    id_remoto: Mapped[str | None] = mapped_column(String(200))
    intentos: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    respuesta_motivo: Mapped[str | None] = mapped_column(Text)
    respuesta_envio: Mapped[str | None] = mapped_column(String(20))
    respuesta_http: Mapped[int | None] = mapped_column(Integer)
    respuesta_detalle: Mapped[str | None] = mapped_column(Text)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actualizado: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
