"""Puerta de entrada de los socios (R29, R31): credencial de sistema, scope, nonce, firma opcional y tamaño.

Orden: tamaño (413) → credencial (401) → institución activa (401) → scope (403) → firma si se acordó (401)
→ nonce (400 formato, 409 repetido; se anota en su propia transacción: se gasta aunque luego se rechace el cuerpo).
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass

import jwt
from fastapi import Request
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.interop import cripto
from app.interop.fhir import InteropError
from app.models.interop import Socio

ALIAS_SCOPE = {"bundle:write": "system/ServiceRequest.write", "task:write": "system/Task.write",
               "system/servicerequest.write": "system/ServiceRequest.write", "system/task.write": "system/Task.write"}
SCOPE_BUNDLE = "system/ServiceRequest.write"
SCOPE_TASK = "system/Task.write"
VENTANA_FIRMA_S = 300


def normalizar_scopes(texto: str | None) -> list[str]:
    salida = []
    for s in (texto or "").replace(",", " ").split():
        s = ALIAS_SCOPE.get(s.lower(), s)
        if s not in salida:
            salida.append(s)
    return salida


def ip_de(request: Request) -> str:
    h = request.headers
    return (h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
            or h.get("x-real-ip") or (request.client.host if request.client else "desconocida"))


@dataclass
class Entrada:
    socio: Socio
    cuerpo: bytes
    nonce: str | None


async def leer_cuerpo(request: Request) -> bytes:
    largo = request.headers.get("content-length")
    if largo and largo.isdigit() and int(largo) > settings.interop_max_bytes:
        raise InteropError(413, f"Cuerpo de más de {settings.interop_max_bytes // 1024} KB", code="too-costly")
    cuerpo = await request.body()
    if len(cuerpo) > settings.interop_max_bytes:
        raise InteropError(413, f"Cuerpo de más de {settings.interop_max_bytes // 1024} KB", code="too-costly")
    return cuerpo


def _credencial(request: Request) -> tuple[str, str | None, list[str] | None]:
    """Devuelve (client_id, secreto o None si vino token, scopes del token o None)."""
    auth = request.headers.get("authorization") or ""
    api_key = request.headers.get("x-api-key")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        try:
            datos = cripto.leer_token(token)
        except jwt.ExpiredSignatureError:
            raise InteropError(401, "Token de sistema vencido: pida otro en /oauth/token", code="security")
        except jwt.InvalidTokenError:
            raise InteropError(401, "Token inválido: use un token de sistema de /oauth/token (un token de usuario no sirve aquí)", code="security")
        return datos["sub"], None, normalizar_scopes(datos.get("scope"))
    if api_key:
        cid, _, secreto = api_key.strip().partition(".")
        if not cid or not secreto:
            raise InteropError(401, "X-API-Key debe ser <client_id>.<client_secret>", code="security")
        return cid, secreto, None
    raise InteropError(401, "Falta la credencial: Authorization: Bearer <token> o X-API-Key", code="security",
                       cabeceras={"WWW-Authenticate": 'Bearer realm="interop"'})


def _anotar_nonce(socio_id: int, nonce: str) -> bool:
    with SessionLocal() as s:
        s.execute(text("DELETE FROM interop_nonces WHERE creado < now() - interval '24 hours'"))
        try:
            s.execute(text("INSERT INTO interop_nonces (socio_id, nonce) VALUES (:s, :n)"), {"s": socio_id, "n": nonce})
            s.commit()
            return True
        except IntegrityError:
            s.rollback()
            return False


async def autenticar(request: Request, db: Session, *, scope: str, ruta: str) -> Entrada:
    cuerpo = await leer_cuerpo(request)
    client_id, secreto, scopes_token = _credencial(request)
    request.state.interop_client_id = client_id
    socio = db.scalar(select(Socio).where(Socio.client_id == client_id))
    if secreto is not None and not cripto.verificar_secreto(secreto, socio.secreto_hash if socio else None):
        raise InteropError(401, "Credencial inválida", code="security")
    if socio is None:
        raise InteropError(401, "Credencial inválida", code="security")
    request.state.interop_socio = socio.codigo
    if socio.estado != "activo":
        raise InteropError(401, "La institución está desactivada", code="security")
    permitidos = scopes_token if scopes_token is not None else normalizar_scopes(socio.scopes)
    permitidos = [s for s in permitidos if s in normalizar_scopes(socio.scopes)]  # revocar scope corta tokens vivos
    if scope not in permitidos:
        raise InteropError(403, f"La credencial no tiene el permiso {scope}", code="forbidden")

    h = request.headers
    nonce = h.get("x-nonce")
    if socio.exige_firma:
        ts, firma = h.get("x-timestamp"), h.get("x-signature")
        if not (ts and nonce and firma):
            raise InteropError(401, "Esta institución firma sus mensajes: faltan X-Timestamp, X-Nonce o X-Signature", code="security")
        if not ts.isdigit() or abs(time.time() - int(ts)) > VENTANA_FIRMA_S:
            raise InteropError(401, f"X-Timestamp fuera de la ventana de {VENTANA_FIRMA_S} s (revise el reloj)", code="security")
        if not cripto.NONCE_RE.match(nonce):
            raise InteropError(400, "X-Nonce inválido: 16 a 128 caracteres A-Z a-z 0-9 _ -", code="value")
        secreto_firma = cripto.descifrar(socio.firma_secreto_cif) or ""
        if not cripto.firma_valida(secreto_firma, firma, ts, nonce, request.method, ruta, cuerpo):
            raise InteropError(401, "Firma inválida", code="security")
    if nonce is not None:
        if not cripto.NONCE_RE.match(nonce):
            raise InteropError(400, "x-nonce inválido: 16 a 128 caracteres A-Z a-z 0-9 _ - (un UUID sirve)", code="value")
        if not _anotar_nonce(socio.id, nonce):
            raise InteropError(409, "x-nonce repetido: este mensaje ya se había enviado", code="duplicate", auditar=True)
    return Entrada(socio, cuerpo, nonce)


def credencial_basic(request: Request) -> tuple[str | None, str | None]:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("basic "):
        return None, None
    try:
        cid, _, sec = base64.b64decode(auth[6:].strip()).decode().partition(":")
        return cid or None, sec or None
    except Exception:
        return None, None
