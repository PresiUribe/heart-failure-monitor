"""Primitivas criptográficas de la interoperabilidad.

- Secretos de socios en reposo: AES-256-GCM (los que necesitamos en claro: firma y credenciales de salida).
- client_secret que les damos: solo su hash Argon2id.
- Token de sistema: JWT HS256 con una clave DISTINTA a la de los usuarios (derivada), aud/iss/type obligatorios.
- Firma HMAC-SHA256 por mensaje (opcional por socio) e id federado HMAC-SHA256.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
import uuid

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

ISS = "hf-monitor"
AUD = "hf-monitor-interop"
_hasher = PasswordHasher()
_HASH_FICTICIO = _hasher.hash(secrets.token_urlsafe(32))  # mismo costo cuando el cliente no existe
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


# ---------- cifrado en reposo ----------
def _clave_cifrado() -> bytes:
    base = settings.interop_clave_cifrado or f"interop-cifrado:{settings.jwt_secret_key}"
    return hashlib.sha256(base.encode()).digest()


def cifrar(texto: str | None) -> str | None:
    if not texto:
        return None
    n = secrets.token_bytes(12)
    ct = AESGCM(_clave_cifrado()).encrypt(n, texto.encode(), b"interop-v1")
    return "v1:" + base64.urlsafe_b64encode(n + ct).decode()


def descifrar(valor: str | None) -> str | None:
    if not valor:
        return None
    if not valor.startswith("v1:"):
        raise ValueError("Formato de secreto cifrado desconocido")
    raw = base64.urlsafe_b64decode(valor[3:].encode())
    return AESGCM(_clave_cifrado()).decrypt(raw[:12], raw[12:], b"interop-v1").decode()


# ---------- credenciales que emitimos ----------
def nuevo_client_id() -> str:
    return "cli_" + secrets.token_hex(12)


def nuevo_secreto() -> str:
    return secrets.token_urlsafe(32)  # sin '.', así la API key client_id.secreto se separa sin ambigüedad


def hash_secreto(secreto: str) -> str:
    return _hasher.hash(secreto)


def verificar_secreto(secreto: str, hash_guardado: str | None) -> bool:
    try:
        return _hasher.verify(hash_guardado or _HASH_FICTICIO, secreto) and hash_guardado is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ---------- token de sistema ----------
def _clave_token() -> str:
    # Derivada: un token de usuario nunca valida aquí y uno de sistema nunca valida en la API clínica.
    return hmac.new(settings.jwt_secret_key.encode(), b"interop-token-v1", hashlib.sha256).hexdigest()


def emitir_token(client_id: str, scopes: list[str]) -> tuple[str, int]:
    ahora = int(time.time())
    dura = settings.interop_token_minutos * 60
    payload = {"iss": ISS, "aud": AUD, "sub": client_id, "scope": " ".join(scopes), "type": "interop",
               "iat": ahora, "exp": ahora + dura, "jti": uuid.uuid4().hex}
    return jwt.encode(payload, _clave_token(), algorithm="HS256"), dura


def leer_token(token: str) -> dict:
    datos = jwt.decode(token, _clave_token(), algorithms=["HS256"], audience=AUD, issuer=ISS,
                       options={"require": ["exp", "iat", "aud", "iss", "sub"]})
    if datos.get("type") != "interop":
        raise jwt.InvalidTokenError("tipo de token incorrecto")
    return datos


# ---------- firma por mensaje ----------
def cadena_firma(ts: str, nonce: str, metodo: str, ruta: str, cuerpo: bytes) -> str:
    return "\n".join(["v1", ts, nonce, metodo.upper(), ruta, hashlib.sha256(cuerpo).hexdigest()])


def firmar(secreto: str, ts: str, nonce: str, metodo: str, ruta: str, cuerpo: bytes) -> str:
    cadena = cadena_firma(ts, nonce, metodo, ruta, cuerpo)
    return "v1=" + hmac.new(secreto.encode(), cadena.encode(), hashlib.sha256).hexdigest()


def firma_valida(secreto: str, firma: str, ts: str, nonce: str, metodo: str, ruta: str, cuerpo: bytes) -> bool:
    return hmac.compare_digest(firmar(secreto, ts, nonce, metodo, ruta, cuerpo), firma or "")


# ---------- id federado ----------
def normalizar_documento(numero: str) -> str:
    return re.sub(r"[\s.\-]", "", str(numero or "")).upper()


def sid_federado(tipo: str, numero: str) -> str:
    """HMAC-SHA256(secreto_federado, "TIPO|documento")[:32]; el mismo cálculo que hacen los socios."""
    dato = f"{(tipo or 'CC').strip().upper()}|{normalizar_documento(numero)}"
    return hmac.new(settings.interop_secreto_federado.encode(), dato.encode(), hashlib.sha256).hexdigest()[:32]
