"""R27 · Cifrado de campos sensibles en PostgreSQL (AES-256-GCM) e índice ciego para buscar sin descifrar.

- La llave vive en la variable de entorno CAMPOS_CLAVE (nunca en el repositorio). Si falta, se deriva de
  JWT_SECRET_KEY (también una variable de entorno) y se avisa en el log: conviene definirla aparte.
- Formato guardado: "gcm1:<id de llave>:<base64(nonce de 12 bytes + texto cifrado + etiqueta)>".
  El nombre de la columna va como dato asociado (AAD): un documento cifrado no se puede pegar en el
  campo teléfono de otro paciente sin que falle la verificación.
- Índice ciego: HMAC-SHA256 con una subllave distinta. Permite "buscar por documento" y la unicidad
  sin guardar el documento en claro.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import settings

log = logging.getLogger(__name__)
PREFIJO = "gcm1:"
_avisado = False


def maestra_de(clave: str | None) -> bytes:
    """Llave maestra a partir del valor de CAMPOS_CLAVE (vacío = derivada de JWT_SECRET_KEY)."""
    if clave:
        return hashlib.sha256(clave.encode()).digest()
    return hashlib.sha256(f"campos-derivada:{settings.jwt_secret_key}".encode()).digest()


def _maestra() -> bytes:
    global _avisado
    if settings.campos_clave:
        return maestra_de(settings.campos_clave)
    if not _avisado:
        log.warning("CAMPOS_CLAVE no está definida: la llave de cifrado de campos se deriva de JWT_SECRET_KEY")
        _avisado = True
    return maestra_de(None)


def _sub(etiqueta: bytes, maestra: bytes | None = None) -> bytes:
    return hmac.new(maestra or _maestra(), etiqueta, hashlib.sha256).digest()


def id_llave(maestra: bytes | None = None) -> str:
    """Huella corta de la llave activa (no revela la llave): detecta si se cambió CAMPOS_CLAVE."""
    return hashlib.sha256(_sub(b"id", maestra)).hexdigest()[:8]


def cifrar_campo(valor: str | None, contexto: str, maestra: bytes | None = None) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, str) and valor.startswith(PREFIJO):
        return valor  # ya cifrado (p. ej. una instantánea de historial)
    n = secrets.token_bytes(12)
    ct = AESGCM(_sub(b"cifrado", maestra)).encrypt(n, str(valor).encode(), contexto.encode())
    return f"{PREFIJO}{id_llave(maestra)}:{base64.urlsafe_b64encode(n + ct).decode()}"


def descifrar_campo(valor: str | None, contexto: str, maestra: bytes | None = None) -> str | None:
    if valor is None or not str(valor).startswith(PREFIJO):
        return valor  # dato heredado aún sin migrar
    _, kid, datos = valor.split(":", 2)
    if kid != id_llave(maestra):
        raise ValueError(f"El campo {contexto} se cifró con otra llave ({kid}); revise CAMPOS_CLAVE "
                         "o rote con scripts/rotar_clave_campos.py")
    raw = base64.urlsafe_b64decode(datos.encode())
    return AESGCM(_sub(b"cifrado", maestra)).decrypt(raw[:12], raw[12:], contexto.encode()).decode()


def normalizar_documento(valor: str) -> str:
    return str(valor or "").strip()


def indice_ciego(valor: str | None, maestra: bytes | None = None) -> str | None:
    if valor is None:
        return None
    return hmac.new(_sub(b"indice-ciego", maestra), normalizar_documento(valor).encode(), hashlib.sha256).hexdigest()


class Cifrado(TypeDecorator):
    """Columna que la aplicación ve en claro y PostgreSQL guarda cifrada."""

    impl = Text
    cache_ok = True

    def __init__(self, contexto: str, *args, **kwargs):
        self.contexto = contexto
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        return cifrar_campo(value, self.contexto)

    def process_result_value(self, value, dialect):
        return descifrar_campo(value, self.contexto)
