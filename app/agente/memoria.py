# app/agente/memoria.py · Memoria del agente en PostgreSQL (checkpointer = hilos, store = preferencias).
#
# Las tablas las crea setup() la primera vez en la misma base clínica (app-db), separadas de la historia
# clínica: el agente CONSULTA la historia con herramientas, nunca la copia a su memoria (Ley 1581/2012).
import logging
from contextlib import AsyncExitStack

from app.agente.harness import Harness, asegurar_tabla_pendientes, construir_agente
from app.config import settings

log = logging.getLogger("agente")


def _url_psycopg() -> str:
    url = settings.database_url
    for prefijo in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgres://"):
        if url.startswith(prefijo):
            url = "postgresql://" + url[len(prefijo):]
    return url


async def iniciar_agente(pila: AsyncExitStack) -> Harness:
    """Crea el harness con memoria en PostgreSQL. Si la base no responde, usa memoria RAM (y lo dice)."""
    asegurar_tabla_pendientes()
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.store.postgres.aio import AsyncPostgresStore
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(conninfo=_url_psycopg(), min_size=1, max_size=10, open=False,
                                   kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
        await pool.open(wait=True, timeout=30)
        pila.push_async_callback(pool.close)
        checkpointer, store = AsyncPostgresSaver(pool), AsyncPostgresStore(pool)
        await checkpointer.setup()
        await store.setup()
        memoria = "postgresql"
    except Exception as exc:  # noqa: BLE001
        log.warning("Agente: memoria en PostgreSQL no disponible (%s); se usa memoria RAM", exc)
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.store.memory import InMemoryStore
        checkpointer, store, memoria = InMemorySaver(), InMemoryStore(), "memoria"
    return Harness(construir_agente(checkpointer=checkpointer, store=store), timeout_s=settings.agente_timeout_s,
                   memoria=memoria)
