# app/agente/modelo.py · Punto único para elegir el modelo del agente.
from app.config import settings
from app.agente.simulado import ModeloSimulado


def nombre_modelo() -> str:
    if settings.agente_modelo == "simulado" or not settings.groq_api_key:
        return "simulado"
    return f"groq:{settings.groq_model}"


def crear_modelo(latencia_simulada: float = 0.5):
    """Groq si hay GROQ_API_KEY (y AGENTE_MODELO no es 'simulado'); si no, el modelo simulado del notebook."""
    if nombre_modelo() == "simulado":
        return ModeloSimulado(latencia=latencia_simulada)
    from langchain_groq import ChatGroq
    extra = {"reasoning_effort": "low"} if "gpt-oss" in settings.groq_model else {}
    return ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        temperature=0,        # en clínica no queremos creatividad
        max_tokens=700,       # cuenta gratuita: limita los tokens de salida por llamada
        max_retries=3,        # 429 (límite de tasa) o 5xx: reintenta con espera
        timeout=30,
        **extra,
    )
