"""Registro de instituciones socias: lo usan la pantalla de admin y scripts/interop_cliente.py."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.interop import cripto
from app.interop.seguridad import normalizar_scopes
from app.models.interop import SCOPES_POR_DEFECTO, Socio

CODIGO_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,59}$")
AUTH_SALIDA = {"oauth2", "api_key", "bearer", "ninguno"}
URL_ESPEJO = "http://localhost:8000"   # la propia API, dentro del contenedor (sin /api)


def validar_url(url: str | None) -> str | None:
    if url is None or url == "":
        return None
    url = url.strip().rstrip("/")
    if not re.match(r"^https?://[^\s/$.?#][^\s]*$", url):
        raise ValueError("La URL debe empezar por http:// o https://")
    return url


def _ruta(r: str | None, defecto: str) -> str:
    r = (r or defecto).strip()
    return r if r.startswith("/") else "/" + r


def resumen(s: Socio) -> dict:
    return {
        "codigo": s.codigo, "nombre": s.nombre, "estado": s.estado, "url_base": s.url_base, "es_espejo": s.es_espejo,
        "entrada": {"client_id": s.client_id, "scopes": normalizar_scopes(s.scopes), "exige_firma": s.exige_firma},
        "salida": {"auth": s.auth_salida, "credencial": bool(s.cred_secreto_salida_cif) or s.auth_salida == "ninguno",
                   "credencial_id": s.cred_id_salida, "scope": s.scope_salida, "firma": s.firma_salida,
                   "ruta_token": s.ruta_token, "ruta_bundle": s.ruta_bundle, "ruta_task": s.ruta_task,
                   "cabecera_api_key": s.cabecera_api_key},
        "lista_para_enviar": s.estado == "activo" and bool(s.url_base) and (s.auth_salida == "ninguno" or bool(s.cred_secreto_salida_cif)),
        "actualizado": s.actualizado,
    }


def crear_socio(db: Session, *, codigo: str, nombre: str, url_base: str | None, con_firma: bool = False,
                scopes: str | None = None, ruta_bundle: str | None = None, ruta_task: str | None = None,
                ruta_token: str | None = None, cabecera: str | None = None, es_espejo: bool = False) -> tuple[Socio, dict]:
    """Crea la institución y SU credencial para entrar a nosotros. Los secretos se devuelven una sola vez."""
    codigo = codigo.strip().lower()
    if not CODIGO_RE.match(codigo):
        raise ValueError("codigo: minúsculas, números, - o _ (2 a 60 caracteres)")
    if db.scalar(select(Socio).where(Socio.codigo == codigo)):
        raise ValueError(f"Ya existe la institución {codigo}")
    client_id, secreto = cripto.nuevo_client_id(), cripto.nuevo_secreto()
    firma = cripto.nuevo_secreto() if con_firma else None
    s = Socio(codigo=codigo, nombre=nombre.strip()[:150], url_base=validar_url(url_base), client_id=client_id,
              secreto_hash=cripto.hash_secreto(secreto), scopes=" ".join(normalizar_scopes(scopes or SCOPES_POR_DEFECTO)),
              exige_firma=con_firma, firma_secreto_cif=cripto.cifrar(firma), es_espejo=es_espejo,
              ruta_bundle=_ruta(ruta_bundle, "/interop/Bundle"), ruta_task=_ruta(ruta_task, "/interop/Task"),
              ruta_token=_ruta(ruta_token, "/oauth/token"), cabecera_api_key=(cabecera or "X-API-Key").strip())
    db.add(s)
    db.flush()
    secretos = {"client_id": client_id, "client_secret": secreto, "api_key": f"{client_id}.{secreto}"}
    if firma:
        secretos["firma_secreto"] = firma
    return s, secretos


def configurar_salida(s: Socio, *, auth: str, credencial_id: str | None = None, credencial_secreto: str | None = None,
                      scope: str | None = None, firma_secreto: str | None = None) -> None:
    """La credencial que ESA institución nos dio para entrar a ella (cifrada en reposo)."""
    if auth not in AUTH_SALIDA:
        raise ValueError(f"auth debe ser uno de {sorted(AUTH_SALIDA)}")
    if auth == "oauth2" and not (credencial_id and credencial_secreto):
        raise ValueError("oauth2 necesita client_id y client_secret del socio")
    if auth in ("api_key", "bearer") and not credencial_secreto:
        raise ValueError(f"{auth} necesita el secreto (la API key o el token)")
    s.auth_salida = auth
    s.cred_id_salida = (credencial_id or None) if auth != "ninguno" else None
    s.cred_secreto_salida_cif = cripto.cifrar(credencial_secreto) if auth != "ninguno" else None
    s.scope_salida = scope or None
    s.firma_salida = bool(firma_secreto)
    s.firma_secreto_salida_cif = cripto.cifrar(firma_secreto)


def rotar_secreto(s: Socio) -> dict:
    secreto = cripto.nuevo_secreto()
    s.secreto_hash = cripto.hash_secreto(secreto)
    return {"client_id": s.client_id, "client_secret": secreto, "api_key": f"{s.client_id}.{secreto}"}


def crear_espejo(db: Session, url: str = URL_ESPEJO, con_firma: bool = False) -> Socio:
    """Nos registra como socio de nosotros mismos: lo que enviamos entra por nuestra propia puerta (ensayo completo)."""
    previo = db.scalar(select(Socio).where(Socio.codigo == "espejo"))
    if previo:  # se conserva el historial: se rotan las credenciales en lugar de borrar
        secretos = rotar_secreto(previo)
        firma = cripto.nuevo_secreto() if con_firma else None
        previo.url_base, previo.estado, previo.es_espejo = validar_url(url), "activo", True
        previo.exige_firma, previo.firma_secreto_cif = con_firma, cripto.cifrar(firma)
        configurar_salida(previo, auth="oauth2", credencial_id=secretos["client_id"], credencial_secreto=secretos["client_secret"],
                          firma_secreto=firma)
        return previo
    s, secretos = crear_socio(db, codigo="espejo", nombre="Espejo (nosotros mismos)", url_base=url, con_firma=con_firma, es_espejo=True)
    configurar_salida(s, auth="oauth2", credencial_id=secretos["client_id"], credencial_secreto=secretos["client_secret"],
                      firma_secreto=secretos.get("firma_secreto"))
    return s
