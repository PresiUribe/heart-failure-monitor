# app/agente/simulado.py · Modelo de práctica (del notebook de la Semana 13, adaptado a insuficiencia cardiaca).
# Imita el FORMATO de un LLM real (pide herramientas o responde), no su inteligencia. Sirve para probar el
# harness sin gastar tokens de Groq: AGENTE_MODELO=simulado.
import asyncio
import re
import time
import uuid

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool


class ModeloSimulado(BaseChatModel):
    latencia: float = 0.5          # segundos que "tarda" en pensar
    herramientas: list = []        # nombres de las herramientas recibidas

    @property
    def _llm_type(self) -> str:
        return "simulado"

    def bind_tools(self, tools, **kwargs):
        nombres = [convert_to_openai_tool(t)["function"]["name"] for t in tools]
        return self.model_copy(update={"herramientas": nombres})

    def _plan(self, texto: str) -> list:
        t, plan = texto.lower(), []

        def usar(nombre, args):
            if nombre in self.herramientas:
                plan.append((nombre, args))

        remitir = re.search(r"rem[ií]t|remisi[oó]n", t)
        riesgo = re.search(r"riesgo|reporte|\bia\b|alerta", t)
        if remitir:
            usar("cargar_skill", {"nombre": "remision-cardiologia"})
        elif riesgo:
            usar("cargar_skill", {"nombre": "interpretar-riesgo-ia"})
        if re.search(r"paciente|este caso|sus datos|historia", t):
            usar("consultar_paciente", {})
        if riesgo or remitir:
            usar("consultar_reportes_ia", {})
        if re.search(r"gu[ií]a|criterio|qu[eé] hago|recomend|cu[aá]ndo|sodio|fevi|creatinina|potasio|"
                     r"hiponatremia|tratamiento|meta|nyha", t):
            usar("buscar_guias", {"pregunta": texto[:200]})
        if re.search(r"recalcul|ejecuta el modelo|calcula el riesgo|corre el modelo", t):
            usar("ejecutar_modelo_riesgo", {})
        if remitir:
            usar("proponer_remision", {"especialidad": "cardiologia", "motivo": texto[:150]})
        if re.search(r"recuerda que|prefiero", t):
            usar("guardar_preferencia", {"preferencia": texto[:160]})
        return plan

    def _responder(self, mensajes) -> AIMessage:
        # Los recordatorios del harness no son preguntas del usuario (el modelo real los atiende con tool_choice).
        humanos = [i for i, m in enumerate(mensajes) if isinstance(m, HumanMessage)
                   and not str(m.content).startswith("[Recordatorio del sistema]")]
        if not humanos:
            return AIMessage(content="[simulado] No veo ninguna pregunta del usuario en el contexto.")
        i = humanos[-1]
        pregunta = mensajes[i].content
        resultados = [m for m in mensajes[i + 1:] if isinstance(m, ToolMessage)]
        plan = self._plan(pregunta)
        if len(resultados) < len(plan):
            nombre, args = plan[len(resultados)]
            llamada = {"name": nombre, "args": args, "id": "call_" + uuid.uuid4().hex[:8], "type": "tool_call"}
            return AIMessage(content="", tool_calls=[llamada])
        if not resultados:
            return AIMessage(content=f"[simulado] Recibí: «{pregunta[:90]}». No necesité herramientas.")
        resumen = "\n".join(f"- {m.name}: {str(m.content)[:220]}" for m in resultados)
        return AIMessage(content="[simulado] Respuesta armada con lo que devolvieron las herramientas:\n" + resumen)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        time.sleep(self.latencia)
        return ChatResult(generations=[ChatGeneration(message=self._responder(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        await asyncio.sleep(self.latencia)
        return ChatResult(generations=[ChatGeneration(message=self._responder(messages))])
