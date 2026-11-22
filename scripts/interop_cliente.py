"""Registro de instituciones socias (misma idea que el script de Pulso).

Se corre DENTRO del contenedor de la API, que es quien tiene la base y las claves:

    docker compose exec api python scripts/interop_cliente.py socio-listar
    docker compose exec api python scripts/interop_cliente.py socio-crear pulso "Pulso · Hospital San Rafael" https://SU-TUNEL.trycloudflare.com/api --sin-firma
    docker compose exec -e SOCIO_CREDENCIAL_ID=cli_... -e SOCIO_CREDENCIAL_SECRETO=... api python scripts/interop_cliente.py socio-credenciales pulso --auth oauth2
    docker compose exec api python scripts/interop_cliente.py socio-url pulso https://NUEVO-TUNEL.trycloudflare.com/api
    docker compose exec api python scripts/interop_cliente.py espejo
    docker compose exec api python scripts/interop_cliente.py federado CC 1144123456

Las credenciales del socio se leen de variables de entorno (no de la línea de comandos: quedarían en el historial).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.interop import cripto, socios as reg  # noqa: E402
from app.interop.esquema import asegurar_esquema_interop  # noqa: E402
from app.models.interop import Socio  # noqa: E402
from app.services.audit import add_audit  # noqa: E402


def _socio(db, codigo):
    s = db.scalar(select(Socio).where(Socio.codigo == codigo.lower()))
    if s is None:
        sys.exit(f"No existe la institución {codigo}. Use socio-listar.")
    return s


def _audit(db, accion, s, **det):
    add_audit(db, user_id=None, action=accion, entity_type="INTEROP", entity_id=None,
              details={"origen": "cli", "institucion": s.codigo, **det})


def _mostrar_secretos(secretos: dict):
    print("\n  Credenciales para ENTREGAR a la institución (se muestran UNA sola vez; canal privado, nunca git ni el chat del curso):")
    for k, v in secretos.items():
        print(f"    {k:14s} {v}")
    print()


def cmd_listar(a):
    with SessionLocal() as db:
        filas = list(db.scalars(select(Socio).order_by(Socio.codigo)))
        if not filas:
            print("Sin instituciones. Cree una con socio-crear o el espejo con: espejo")
        for s in filas:
            r = reg.resumen(s)
            print(f"{s.codigo:14s} {s.estado:9s} {s.url_base or '(sin URL)'}")
            print(f"{'':14s} sale_con={s.auth_salida} credencial={'sí' if r['salida']['credencial'] else 'NO'} "
                  f"firma_salida={'sí' if s.firma_salida else 'no'} lista={'sí' if r['lista_para_enviar'] else 'NO'}")
            print(f"{'':14s} entra_con={s.client_id[:16]}… firma_entrada={'sí' if s.exige_firma else 'no'} "
                  f"rutas: token={s.ruta_token} bundle={s.ruta_bundle} task={s.ruta_task}")


def cmd_crear(a):
    with SessionLocal() as db:
        try:
            s, secretos = reg.crear_socio(db, codigo=a.codigo, nombre=a.nombre, url_base=a.url, con_firma=a.con_firma,
                                          ruta_bundle=a.ruta_bundle, ruta_task=a.ruta_task, ruta_token=a.ruta_token, cabecera=a.cabecera)
        except ValueError as e:
            sys.exit(str(e))
        _audit(db, "INTEROP_SOCIO_CREADO", s, firma=s.exige_firma)
        db.commit()
        print(f"Institución {s.codigo} creada ({s.nombre}).")
        _mostrar_secretos(secretos)
        print("  Siguiente paso: cargar la credencial que ELLOS nos dan, con socio-credenciales.")


def cmd_credenciales(a):
    cid = os.environ.get("SOCIO_CREDENCIAL_ID")
    sec = os.environ.get("SOCIO_CREDENCIAL_SECRETO")
    firma = os.environ.get("SOCIO_FIRMA_SECRETO")
    with SessionLocal() as db:
        s = _socio(db, a.codigo)
        try:
            reg.configurar_salida(s, auth=a.auth, credencial_id=cid, credencial_secreto=sec, scope=a.scope, firma_secreto=firma)
        except ValueError as e:
            sys.exit(f"{e}. Pase los valores con -e SOCIO_CREDENCIAL_ID=... -e SOCIO_CREDENCIAL_SECRETO=... [-e SOCIO_FIRMA_SECRETO=...]")
        for campo in ("ruta_bundle", "ruta_task", "ruta_token"):
            if getattr(a, campo):
                setattr(s, campo, "/" + getattr(a, campo).lstrip("/"))
        if a.cabecera:
            s.cabecera_api_key = a.cabecera
        _audit(db, "INTEROP_SOCIO_SALIDA", s, auth=a.auth, firma=bool(firma))
        db.commit()
        print(f"Salida hacia {s.codigo}: auth={a.auth}{' + firma' if firma else ''}. Lista para enviar: {reg.resumen(s)['lista_para_enviar']}")


def cmd_url(a):
    with SessionLocal() as db:
        s = _socio(db, a.codigo)
        try:
            s.url_base = reg.validar_url(a.url)
        except ValueError as e:
            sys.exit(str(e))
        _audit(db, "INTEROP_SOCIO_ACTUALIZADO", s, campos=["url_base"])
        db.commit()
        print(f"{s.codigo}: URL = {s.url_base}")


def cmd_estado(a, estado):
    with SessionLocal() as db:
        s = _socio(db, a.codigo)
        s.estado = estado
        _audit(db, "INTEROP_SOCIO_ACTUALIZADO", s, campos=["estado"], estado=estado)
        db.commit()
        print(f"{s.codigo}: {estado} (desactivar corta de inmediato sus tokens vivos)")


def cmd_rotar(a):
    with SessionLocal() as db:
        s = _socio(db, a.codigo)
        secretos = reg.rotar_secreto(s)
        _audit(db, "INTEROP_SOCIO_ROTADO", s)
        db.commit()
        _mostrar_secretos(secretos)


def cmd_espejo(a):
    with SessionLocal() as db:
        s = reg.crear_espejo(db, url=a.url, con_firma=a.con_firma)
        _audit(db, "INTEROP_ESPEJO", s, firma=a.con_firma)
        db.commit()
        print(f"Espejo listo: {s.url_base} (oauth2{' + firma' if a.con_firma else ''}). Pruebe con: python scripts/test_interop.py")


def cmd_federado(a):
    print(cripto.sid_federado(a.tipo, a.documento))


def main():
    p = argparse.ArgumentParser(description="Instituciones socias (interoperabilidad)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("socio-listar")
    c = sub.add_parser("socio-crear", help="crea la institución y la credencial con la que ELLA entra a nosotros")
    c.add_argument("codigo"); c.add_argument("nombre"); c.add_argument("url", nargs="?", default=None)
    g = c.add_mutually_exclusive_group()
    g.add_argument("--con-firma", dest="con_firma", action="store_true", help="exigirles firma HMAC por mensaje")
    g.add_argument("--sin-firma", dest="con_firma", action="store_false")
    for x in ("--ruta-bundle", "--ruta-task", "--ruta-token", "--cabecera"):
        c.add_argument(x)
    k = sub.add_parser("socio-credenciales", help="la credencial que ELLOS nos dieron (variables SOCIO_CREDENCIAL_ID/SECRETO)")
    k.add_argument("codigo"); k.add_argument("--auth", required=True, choices=sorted(reg.AUTH_SALIDA)); k.add_argument("--scope")
    for x in ("--ruta-bundle", "--ruta-task", "--ruta-token", "--cabecera"):
        k.add_argument(x)
    u = sub.add_parser("socio-url", help="su túnel cambió de URL"); u.add_argument("codigo"); u.add_argument("url")
    for nombre in ("socio-activar", "socio-desactivar", "socio-rotar"):
        sub.add_parser(nombre).add_argument("codigo")
    e = sub.add_parser("espejo", help="nos registra como socio de nosotros mismos")
    e.add_argument("--url", default=reg.URL_ESPEJO); e.add_argument("--con-firma", action="store_true")
    f = sub.add_parser("federado", help="calcula el id federado de un documento"); f.add_argument("tipo"); f.add_argument("documento")
    a = p.parse_args()
    asegurar_esquema_interop()
    {"socio-listar": cmd_listar, "socio-crear": cmd_crear, "socio-credenciales": cmd_credenciales, "socio-url": cmd_url,
     "socio-activar": lambda x: cmd_estado(x, "activo"), "socio-desactivar": lambda x: cmd_estado(x, "inactivo"),
     "socio-rotar": cmd_rotar, "espejo": cmd_espejo, "federado": cmd_federado}[a.cmd](a)


if __name__ == "__main__":
    main()
