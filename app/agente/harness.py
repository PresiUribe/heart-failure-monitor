# app/agente/harness.py · El arnés del agente clínico (Semana 13).
#
# Modelo + prompt por capas + herramientas + skills + memoria + aprobación humana + auditoría + límites.
# Estructura del notebook del curso, adaptada al proyecto: auditoría en la tabla audit_logs (R13),
# memoria en PostgreSQL y pendientes de aprobación persistidos (sobreviven a un reinicio de la API).
import asyncio
import json
import re
import threading
import time
import uuid
from collections import defaultdict

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, HumanInTheLoopMiddleware, ModelCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, trim_messages
from langgraph.types import Command
from sqlalchemy import text

from app.agente import inyeccion, skills
from app.agente.herramientas import HERRAMIENTAS, ROLES_CLINICOS, ContextoClinico, espacio_memoria
from app.agente.modelo import crear_modelo, nombre_modelo
from app.agente.prompt import armar_prompt
from app.config import settings
from app.database import SessionLocal
from app.services.audit import add_audit


# ---------- 1. Prompt dinámico: se arma en CADA llamada al modelo ----------
class PromptClinico(AgentMiddleware):
    """Rol, paciente (seudónimo), índice de skills y preferencias del store. Versión async porque el store
    de PostgreSQL es asíncrono (una llamada síncrona desde el event loop lo bloquearía)."""

    @staticmethod
    def _armar(request, preferencias):
        prompt = armar_prompt(request.runtime.context, skills.indice(), preferencias)
        return request.override(system_message=SystemMessage(prompt))

    def wrap_model_call(self, request, handler):
        prefs = []
        if request.runtime.store is not None:
            items = request.runtime.store.search(espacio_memoria(request.runtime.context.usuario), limit=50)
            prefs = [i.value["texto"] for i in sorted(items, key=lambda i: i.updated_at)[-5:]]
        return handler(self._armar(request, prefs))

    async def awrap_model_call(self, request, handler):
        prefs = []
        if request.runtime.store is not None:
            items = await request.runtime.store.asearch(espacio_memoria(request.runtime.context.usuario), limit=50)
            items = sorted(items, key=lambda i: i.updated_at)[-5:]          # las 5 más recientes
            prefs = [i.value["texto"] for i in items]
        return await handler(self._armar(request, prefs))


# ---------- 2. Auditoría de cada herramienta (R13) ----------
def _auditar_herramienta(ctx: ContextoClinico, nombre: str, args: dict, resultado: str, ms: int) -> None:
    with SessionLocal() as db:
        add_audit(db, user_id=uuid.UUID(ctx.usuario_id), action="AGENTE_HERRAMIENTA", entity_type="AGENTE",
                  entity_id=None,
                  details={"herramienta": nombre, "args": args, "rol": ctx.rol, "paciente": ctx.paciente_alias,
                           "conversacion": ctx.conversacion, "resultado": resultado[:400], "duracion_ms": ms})
        db.commit()


def _inyeccion_indirecta(ctx: ContextoClinico, herramienta: str, salida):
    """R25: si un dato devuelto por una herramienta trae instrucciones, se marca como dato no confiable y se audita."""
    if not isinstance(salida, ToolMessage) or not isinstance(salida.content, str):
        return salida
    patrones = inyeccion.detectar(salida.content)
    if not patrones:
        return salida
    inyeccion.registrar(usuario_id=uuid.UUID(ctx.usuario_id), usuario=ctx.usuario, rol=ctx.rol, ip=ctx.ip or "interna",
                        origen="herramienta", patrones=patrones, extracto=f"{herramienta}: {salida.content[:250]}")
    with SessionLocal() as db:
        add_audit(db, user_id=uuid.UUID(ctx.usuario_id), action="AGENTE_INYECCION_INDIRECTA", entity_type="AGENTE", entity_id=None,
                  details={"herramienta": herramienta, "patrones": patrones, "paciente": ctx.paciente_alias,
                           "conversacion": ctx.conversacion})
        db.commit()
    return salida.model_copy(update={"content": inyeccion.neutralizar_dato(salida.content, patrones)})


class Auditoria(AgentMiddleware):
    """Registra quién usó qué herramienta, con qué argumentos y qué devolvió. Guarda el seudónimo, nunca el id.
    Además neutraliza instrucciones escondidas en los datos que devuelve la herramienta (R25)."""

    @staticmethod
    def _contenido(salida) -> str:
        return str(getattr(salida, "content", salida))

    def wrap_tool_call(self, request, handler):
        inicio = time.perf_counter()
        salida = handler(request)
        _auditar_herramienta(request.runtime.context, request.tool_call["name"], request.tool_call["args"],
                             self._contenido(salida), int((time.perf_counter() - inicio) * 1000))
        return _inyeccion_indirecta(request.runtime.context, request.tool_call["name"], salida)

    async def awrap_tool_call(self, request, handler):
        inicio = time.perf_counter()
        salida = await handler(request)
        await asyncio.to_thread(_auditar_herramienta, request.runtime.context, request.tool_call["name"],
                                request.tool_call["args"], self._contenido(salida),
                                int((time.perf_counter() - inicio) * 1000))
        return await asyncio.to_thread(_inyeccion_indirecta, request.runtime.context, request.tool_call["name"], salida)


# ---------- 2b. Payload exacto al LLM: se audita y se verifica que no lleve datos personales (R24, R12) ----------
MAX_AUDITORIA_PAYLOAD = 12000


def _identificadores(ctx: ContextoClinico) -> list[str]:
    if not ctx.paciente_id:
        return []
    from app.models.patient import Patient
    from app.rag.seudonimizar import identificadores_paciente
    with SessionLocal() as db:
        p = db.get(Patient, uuid.UUID(ctx.paciente_id))
        return identificadores_paciente(p) if p else []


def _sin_identificadores(ctx: ContextoClinico, mensajes: list) -> tuple[list, list[str]]:
    """Última barrera antes del proveedor: si algún identificador del paciente sobrevivió, se reemplaza por el seudónimo."""
    from app.rag.seudonimizar import seudonimizar
    ids = _identificadores(ctx)
    if not ids:
        return mensajes, []
    salida, fugas = [], []
    for m in mensajes:
        contenido = getattr(m, "content", None)
        if isinstance(contenido, str):
            bajo = contenido.lower()
            presentes = [i for i in ids if i.lower() in bajo]
            if presentes:
                fugas += presentes
                m = m.model_copy(update={"content": seudonimizar(contenido, ids, ctx.paciente_alias or "PSN-?")})
        salida.append(m)
    return salida, fugas


def _auditar_payload(ctx: ContextoClinico, sistema: str, mensajes: list, fugas: list[str], modelo: str) -> None:
    import hashlib
    partes = [{"rol": "system", "texto": sistema}] + [
        {"rol": getattr(m, "type", "?"), "texto": _texto(getattr(m, "content", "")),
         **({"herramientas": [t["name"] for t in m.tool_calls]} if isinstance(m, AIMessage) and m.tool_calls else {})}
        for m in mensajes]
    completo = json.dumps(partes, ensure_ascii=False)
    with SessionLocal() as db:
        add_audit(db, user_id=uuid.UUID(ctx.usuario_id), action="AGENTE_LLM_PAYLOAD", entity_type="AGENTE", entity_id=None,
                  details={"modelo": modelo, "paciente": ctx.paciente_alias, "conversacion": ctx.conversacion, "rol": ctx.rol,
                           "mensajes": len(partes), "caracteres": len(completo),
                           "sha256": hashlib.sha256(completo.encode()).hexdigest(),
                           "fugas_bloqueadas": len(fugas),       # nunca se guarda el valor que se bloqueó
                           "payload": completo[:MAX_AUDITORIA_PAYLOAD]})
        db.commit()


class AuditoriaLLM(AgentMiddleware):
    """Va justo antes del proveedor: lo que se audita es exactamente lo que se envía."""

    def _preparar(self, request):
        ctx = request.runtime.context
        mensajes, fugas = _sin_identificadores(ctx, list(request.messages))
        if fugas:
            request = request.override(messages=mensajes)
        sistema = _texto(request.system_message.content) if request.system_message is not None else ""
        return request, (ctx, sistema, list(request.messages), fugas)

    def wrap_model_call(self, request, handler):
        request, datos = self._preparar(request)
        _auditar_payload(*datos, nombre_modelo())
        return handler(request)

    async def awrap_model_call(self, request, handler):
        request, datos = await asyncio.to_thread(self._preparar, request)
        await asyncio.to_thread(_auditar_payload, *datos, nombre_modelo())
        return await handler(request)


# ---------- 3. Ventana de contexto (memoria de trabajo) ----------
class VentanaContexto(AgentMiddleware):
    def __init__(self, max_mensajes: int = 12):
        super().__init__()
        self.max_mensajes = max_mensajes

    def _recortar(self, request):
        # start_on="human": nunca deja un ToolMessage sin el AIMessage que lo pidió.
        mensajes = request.messages
        recientes = trim_messages(mensajes, max_tokens=self.max_mensajes, token_counter=len,
                                  strategy="last", start_on="human")
        # Si el turno actual es más largo que la ventana (muchas herramientas), trim_messages devolvería una lista
        # vacía: el modelo perdería la petición del médico. El turno actual nunca se corta.
        humanos = [i for i, m in enumerate(mensajes) if isinstance(m, HumanMessage)]
        if humanos and not any(isinstance(m, HumanMessage) for m in recientes):
            recientes = mensajes[humanos[-1]:]
        return request.override(messages=recientes)

    def wrap_model_call(self, request, handler):
        return handler(self._recortar(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._recortar(request))


# ---------- 3b. Recordatorio de acción (guardrail) ----------
PIDE_REMITIR = re.compile(r"rem[ií]t|remisi[oó]n", re.I)


def _ultimo_ai(respuesta):
    if isinstance(respuesta, AIMessage):
        return respuesta
    return next((m for m in reversed(getattr(respuesta, "result", []) or []) if isinstance(m, AIMessage)), None)


class RecordatorioAccion(AgentMiddleware):
    """Si el médico pidió remitir y el modelo contestó solo con texto, el harness repite UNA vez la llamada al modelo
    obligándolo a usar `proponer_remision` (tool_choice). Observado con gpt-oss en Groq: "propone" en texto y la
    tarjeta de aprobación nunca aparece. El modelo sigue redactando el motivo y la remisión sigue pasando por la
    aprobación del médico, que puede rechazarla."""

    RECORDATORIO = ("[Recordatorio del sistema] El médico pidió remitir: la propuesta se hace llamando a "
                    "`proponer_remision` con especialidad y un motivo que cite el criterio [n] encontrado (o que diga que "
                    "no hay criterio en las guías). El sistema le mostrará al médico los botones Aprobar y Rechazar.")

    @staticmethod
    def _necesita(request, respuesta) -> bool:
        ctx = request.runtime.context
        if ctx.rol != "medico" or ctx.paciente_id is None:
            return False
        msgs = request.messages
        humanos = [i for i, m in enumerate(msgs) if isinstance(m, HumanMessage)]
        if not humanos or not PIDE_REMITIR.search(_texto(msgs[humanos[-1]].content)):
            return False
        turno = msgs[humanos[-1]:]
        if any(isinstance(m, ToolMessage) and m.name == "proponer_remision" for m in turno):
            return False                       # ya se propuso (aprobada o rechazada)
        ai = _ultimo_ai(respuesta)
        return ai is not None and not ai.tool_calls

    def _con_recordatorio(self, request, respuesta):
        return request.override(messages=[*request.messages, _ultimo_ai(respuesta), HumanMessage(self.RECORDATORIO)],
                                tool_choice="proponer_remision")

    def wrap_model_call(self, request, handler):
        respuesta = handler(request)
        if self._necesita(request, respuesta):
            segunda = handler(self._con_recordatorio(request, respuesta))
            if (_ultimo_ai(segunda) and _ultimo_ai(segunda).tool_calls):
                return segunda
        return respuesta

    async def awrap_model_call(self, request, handler):
        respuesta = await handler(request)
        if self._necesita(request, respuesta):
            segunda = await handler(self._con_recordatorio(request, respuesta))
            if (_ultimo_ai(segunda) and _ultimo_ai(segunda).tool_calls):
                return segunda
        return respuesta


# ---------- 3b'. Exigir evidencia (R23) ----------
SIN_EVIDENCIA_NECESARIA = re.compile(r"^\s*(hola|buen[oa]s|gracias|ok|listo|ad[ií]os|chao)\b|recuerda que|prefiero|"
                                     r"\b(apr[uo]eb|rechaz)", re.I)
# Cualquiera de estas deja fuentes (guías/normas o la historia aprobada); si ya se usó una, no se fuerza otra llamada
# (en la cuenta gratuita de Groq cada llamada extra cuesta tokens por minuto).
HERRAMIENTAS_DE_EVIDENCIA = {"buscar_guias", "consultar_paciente", "consultar_reportes_ia"}


class ExigirEvidencia(AgentMiddleware):
    """Si el clínico hizo una pregunta y el modelo iba a responder sin haber consultado las guías, el harness repite
    UNA vez la llamada obligándolo a usar `buscar_guias`. Así cada respuesta clínica sale con fuentes citadas, o con
    "no hay evidencia" si el RAG se abstiene."""

    RECORDATORIO = ("[Recordatorio del sistema] Antes de responder, busca la evidencia con `buscar_guias` (una búsqueda "
                    "concreta sobre la pregunta) y cita los fragmentos [n]. Si no hay evidencia, dilo.")

    @staticmethod
    def _necesita(request, respuesta) -> bool:
        ctx = request.runtime.context
        nombres = {getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None) for t in request.tools or []}
        if ctx.rol not in ROLES_CLINICOS or "buscar_guias" not in nombres:
            return False
        msgs = request.messages
        humanos = [i for i, m in enumerate(msgs) if isinstance(m, HumanMessage)]
        if not humanos:
            return False
        pregunta = _texto(msgs[humanos[-1]].content)
        if pregunta.startswith("[Recordatorio del sistema]") or SIN_EVIDENCIA_NECESARIA.search(pregunta) or len(pregunta) < 12:
            return False
        if PIDE_REMITIR.search(pregunta):
            return False  # la remisión ya la conduce RecordatorioAccion; una llamada más solo gasta cuota y tiempo
        turno = msgs[humanos[-1]:]
        if any(isinstance(m, ToolMessage) and m.name in HERRAMIENTAS_DE_EVIDENCIA | {"guardar_preferencia", "proponer_remision"}
               for m in turno):
            return False
        ai = _ultimo_ai(respuesta)
        return ai is not None and not ai.tool_calls

    def _con_recordatorio(self, request, respuesta):
        return request.override(messages=[*request.messages, _ultimo_ai(respuesta), HumanMessage(self.RECORDATORIO)],
                                tool_choice="buscar_guias")

    def wrap_model_call(self, request, handler):
        respuesta = handler(request)
        if self._necesita(request, respuesta):
            segunda = handler(self._con_recordatorio(request, respuesta))
            if _ultimo_ai(segunda) and _ultimo_ai(segunda).tool_calls:
                return segunda
        return respuesta

    async def awrap_model_call(self, request, handler):
        respuesta = await handler(request)
        if self._necesita(request, respuesta):
            segunda = await handler(self._con_recordatorio(request, respuesta))
            if _ultimo_ai(segunda) and _ultimo_ai(segunda).tool_calls:
                return segunda
        return respuesta


# ---------- 3c. Reintento ante llamadas de herramienta mal formadas ----------
def _reintentable(exc: Exception) -> bool:
    """Groq responde 400 'tool_use_failed' cuando el modelo genera una llamada de herramienta inválida; repetir suele bastar."""
    texto = str(exc)
    return type(exc).__name__ == "BadRequestError" and ("tool_use_failed" in texto or "Failed to call a function" in texto
                                                         or "parse" in texto.lower())


class ReintentoModelo(AgentMiddleware):
    def __init__(self, intentos: int = 2):
        super().__init__()
        self.intentos = intentos

    def wrap_model_call(self, request, handler):
        for i in range(self.intentos + 1):
            try:
                return handler(request)
            except Exception as exc:  # noqa: BLE001
                if i == self.intentos or not _reintentable(exc):
                    raise

    async def awrap_model_call(self, request, handler):
        for i in range(self.intentos + 1):
            try:
                return await handler(request)
            except Exception as exc:  # noqa: BLE001
                if i == self.intentos or not _reintentable(exc):
                    raise


# ---------- 4. Semáforo: máximo de llamadas simultáneas al proveedor ----------
class LimiteProveedor(AgentMiddleware):
    def __init__(self, maximo: int = 3):
        super().__init__()
        self.maximo = maximo
        self._sem_async = asyncio.Semaphore(maximo)
        self._sem_hilos = threading.Semaphore(maximo)

    def wrap_model_call(self, request, handler):
        with self._sem_hilos:
            return handler(request)

    async def awrap_model_call(self, request, handler):
        async with self._sem_async:          # el que no cabe espera su turno; no recibe 429
            return await handler(request)


# ---------- 5. Armar el agente ----------
def construir_agente(checkpointer=None, store=None, latencia_simulada: float = 0.5, max_simultaneas: int | None = None):
    return create_agent(
        model=crear_modelo(latencia_simulada),
        tools=HERRAMIENTAS,
        context_schema=ContextoClinico,
        middleware=[
            PromptClinico(),
            VentanaContexto(max_mensajes=settings.agente_ventana_mensajes),
            RecordatorioAccion(),
            ExigirEvidencia(),
            ReintentoModelo(intentos=2),
            LimiteProveedor(maximo=max_simultaneas or settings.agente_max_simultaneas),
            AuditoriaLLM(),
            ModelCallLimitMiddleware(run_limit=settings.agente_max_llamadas_modelo, exit_behavior="end"),
            HumanInTheLoopMiddleware(interrupt_on={
                "proponer_remision": {"allowed_decisions": ["approve", "edit", "reject"]},
            }),
            Auditoria(),
        ],
        checkpointer=checkpointer,
        store=store,
    )


# ---------- 6. Pendientes de aprobación en PostgreSQL ----------
def asegurar_tabla_pendientes() -> None:
    with SessionLocal() as db:
        db.execute(text("""CREATE TABLE IF NOT EXISTS agente_pendientes (
            hilo TEXT PRIMARY KEY, contexto JSONB NOT NULL, acciones JSONB, creado TIMESTAMP NOT NULL DEFAULT now())"""))
        db.commit()


def _guardar_pendiente(hilo: str, ctx: ContextoClinico, acciones: list) -> None:
    with SessionLocal() as db:
        db.execute(text("""INSERT INTO agente_pendientes (hilo, contexto, acciones) VALUES (:h, CAST(:c AS jsonb), CAST(:a AS jsonb))
                           ON CONFLICT (hilo) DO UPDATE SET contexto = EXCLUDED.contexto, acciones = EXCLUDED.acciones, creado = now()"""),
                   {"h": hilo, "c": json.dumps(ctx.__dict__), "a": json.dumps(acciones, ensure_ascii=False)})
        db.commit()


def _leer_pendiente(hilo: str) -> tuple[ContextoClinico, list] | None:
    with SessionLocal() as db:
        fila = db.execute(text("SELECT contexto, acciones FROM agente_pendientes WHERE hilo = :h"), {"h": hilo}).first()
    return (ContextoClinico(**fila[0]), fila[1] or []) if fila else None


def _borrar_pendiente(hilo: str) -> None:
    with SessionLocal() as db:
        db.execute(text("DELETE FROM agente_pendientes WHERE hilo = :h"), {"h": hilo})
        db.commit()


# ---------- 7. El harness que atiende solicitudes concurrentes ----------
ROLES_QUE_APRUEBAN = {"medico"}   # R08: solo el médico crea remisiones


def _texto(contenido) -> str:
    if isinstance(contenido, list):   # algunos proveedores devuelven bloques
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in contenido)
    return str(contenido)


class Harness:
    def __init__(self, agente, timeout_s: float = 90, memoria: str = "memoria"):
        self.agente = agente
        self.timeout_s = timeout_s
        self.memoria = memoria                         # "postgresql" o "memoria" (respaldo)
        self._candados = defaultdict(asyncio.Lock)    # un candado por hilo

    @staticmethod
    def hilo(usuario: str, conversacion: str) -> str:
        return f"{usuario}:{conversacion}"            # el usuario sale del token, nunca del cuerpo

    @staticmethod
    def _fuentes_del_turno(mensajes) -> list[dict]:
        """R23: documento y fragmento de lo que de verdad consultaron las herramientas en este turno."""
        ultimo = max((i for i, m in enumerate(mensajes) if isinstance(m, HumanMessage)), default=0)
        fuentes, vistas = [], set()
        for m in mensajes[ultimo:]:
            if isinstance(m, ToolMessage) and isinstance(getattr(m, "artifact", None), list):
                for f in m.artifact:
                    clave = (f.get("chunk_id") or f.get("documento"), f.get("fragmento", "")[:60])
                    if clave not in vistas:
                        vistas.add(clave)
                        fuentes.append({**f, "herramienta": m.name})
        return fuentes

    @staticmethod
    def _herramientas_del_turno(mensajes) -> list[str]:
        ultimo = max((i for i, m in enumerate(mensajes) if isinstance(m, HumanMessage)), default=0)
        return [t["name"] for m in mensajes[ultimo:] if isinstance(m, AIMessage) for t in (m.tool_calls or [])]

    async def _ejecutar(self, entrada, ctx: ContextoClinico, hilo: str) -> dict:
        config = {"configurable": {"thread_id": hilo}}
        inicio = time.perf_counter()
        async with self._candados[hilo]:                       # mismo hilo: un mensaje a la vez
            try:
                salida = await asyncio.wait_for(
                    self.agente.ainvoke(entrada, config, context=ctx, version="v2"), timeout=self.timeout_s)
            except asyncio.TimeoutError:
                return {"estado": "tiempo_agotado", "conversacion": hilo,
                        "detalle": f"El agente no respondió en {self.timeout_s:.0f} s. Intente de nuevo."}
            except Exception as exc:                            # 429 tras reintentos, proveedor caído...
                limite = "RateLimit" in type(exc).__name__ or "429" in str(exc)
                return {"estado": "error_proveedor", "conversacion": hilo,
                        "detalle": ("El proveedor del modelo alcanzó su límite de uso (429). Intente de nuevo en un minuto."
                                    if limite else f"{type(exc).__name__}: el modelo no pudo responder ({str(exc)[:160]}). "
                                                   "Intente de nuevo.")}
        mensajes = salida.value["messages"]
        herramientas = self._herramientas_del_turno(mensajes)
        fuentes = self._fuentes_del_turno(mensajes)
        base = {"conversacion": hilo, "herramientas": herramientas, "fuentes": fuentes,
                "sin_evidencia": "buscar_guias" in herramientas and not any(f.get("tipo") != "historia" for f in fuentes),
                "duracion_s": round(time.perf_counter() - inicio, 2)}
        if salida.interrupts:
            acciones = [{"herramienta": a["name"], "args": a["args"]}
                        for a in salida.interrupts[0].value["action_requests"]]
            await asyncio.to_thread(_guardar_pendiente, hilo, ctx, acciones)
            return {"estado": "pendiente_aprobacion", "acciones": acciones, **base}
        await asyncio.to_thread(_borrar_pendiente, hilo)
        return {"estado": "respondido", "respuesta": _texto(mensajes[-1].content), **base}

    async def atender(self, ctx: ContextoClinico, mensaje: str) -> dict:
        hilo = self.hilo(ctx.usuario, ctx.conversacion)
        if await asyncio.to_thread(_leer_pendiente, hilo) is not None:
            return {"estado": "pendiente_aprobacion", "conversacion": hilo,
                    "detalle": "Hay una acción esperando la decisión del clínico (POST /agente/decidir)."}
        return await self._ejecutar({"messages": [{"role": "user", "content": mensaje}]}, ctx, hilo)

    async def decidir(self, usuario: str, rol: str, conversacion: str, decision: str, motivo: str | None = None) -> dict:
        hilo = self.hilo(usuario, conversacion)
        pendiente = await asyncio.to_thread(_leer_pendiente, hilo)   # sobrevive a un reinicio de la API
        if pendiente is None:
            return {"estado": "sin_pendientes", "conversacion": hilo}
        ctx, acciones = pendiente
        if rol not in ROLES_QUE_APRUEBAN:
            return {"estado": "no_autorizado", "conversacion": hilo}
        d = {"type": decision}
        if decision == "reject":
            d["message"] = motivo or "El clínico rechazó la acción."
        # Una decisión por acción pendiente (el modelo pudo proponer más de una en el mismo paso).
        return await self._ejecutar(Command(resume={"decisions": [d] * max(len(acciones), 1)}), ctx, hilo)

    async def borrar_preferencias(self, usuario: str) -> int:
        """Derecho de supresión (Ley 1581/2012): el usuario borra lo que el agente recuerda de él."""
        store = getattr(self.agente, "store", None)
        if store is None:
            return 0
        items = await store.asearch(espacio_memoria(usuario), limit=500)
        for item in items:
            await store.adelete(item.namespace, item.key)
        return len(items)

    async def pendiente(self, usuario: str, conversacion: str) -> list | None:
        """Acciones esperando decisión en este hilo (para que el frontend muestre la tarjeta al recargar)."""
        p = await asyncio.to_thread(_leer_pendiente, self.hilo(usuario, conversacion))
        return p[1] if p else None

    async def historial(self, usuario: str, conversacion: str) -> list[dict]:
        estado = await self.agente.aget_state({"configurable": {"thread_id": self.hilo(usuario, conversacion)}})
        salida = []
        for m in estado.values.get("messages", []):
            if isinstance(m, HumanMessage):
                salida.append({"rol": "usuario", "texto": _texto(m.content)[:500]})
            elif isinstance(m, AIMessage) and m.tool_calls:
                salida += [{"rol": "agente_pide", "texto": f"{t['name']}({t['args']})"} for t in m.tool_calls]
            elif isinstance(m, ToolMessage):
                salida.append({"rol": "herramienta", "texto": _texto(m.content)[:300]})
            elif isinstance(m, AIMessage):
                salida.append({"rol": "agente", "texto": _texto(m.content)[:800]})
        return salida


__all__ = ["Harness", "construir_agente", "asegurar_tabla_pendientes", "ROLES_CLINICOS"]
