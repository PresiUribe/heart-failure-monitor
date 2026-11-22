# app/agente/rag_conector.py · Une el RAG de la Semana 12 con el agente.
# Forma B del notebook (solo recuperación): el RAG devuelve fragmentos y el agente redacta una sola vez.
# Así no hay dos llamadas al LLM por pregunta, que en la cuenta gratuita de Groq cuestan tokens por minuto.
import uuid

from app.config import settings
from app.database import SessionLocal
from app.models.patient import Patient
from app.rag import indice

MAX_CARACTERES = 350   # lo que devuelve una herramienta entra al prompt y cuesta tokens


async def consultar_rag(pregunta: str, alias: str | None = None, paciente_id: str | None = None, k: int = 3) -> dict:
    """Busca en guías/normas y, si hay paciente abierto, en SU historia aprobada (filtro SQL del RAG).

    alias y paciente_id los pone el harness desde el contexto; el modelo nunca los escribe.
    """
    with SessionLocal() as db:
        if paciente_id is not None:
            patient = db.get(Patient, uuid.UUID(paciente_id))
            if patient is not None:
                await indice.indexar_paciente(db, patient)   # historia al día, seudonimizada antes de indexar
        return await indice.recuperar(db, pregunta, alias, settings.rag_metodo, k)


def formatear_para_agente(resultado: dict) -> str:
    """Fragmentos numerados [1], [2]... con su identificador y fuente."""
    if resultado.get("abstencion") or not resultado.get("fragmentos"):
        return "SIN_EVIDENCIA: no hay evidencia suficiente en las fuentes del sistema para esa pregunta."
    lineas = ["Fragmentos recuperados (cítalos con [n]):"]
    for n, f in enumerate(resultado["fragmentos"], start=1):
        texto = f["texto"] if len(f["texto"]) <= MAX_CARACTERES else f["texto"][:MAX_CARACTERES].rsplit(" ", 1)[0] + "..."
        lineas.append(f"[{n}] ({f['chunk_id']}, {f['tipo']}, {f['estado']}) {texto}")
    return "\n".join(lineas)


def fuentes_de(resultado: dict) -> list[dict]:
    """R23: documento y fragmento de cada fuente usada, numerados igual que los [n] que ve el modelo."""
    if resultado.get("abstencion"):
        return []
    return [{"n": n, "documento": f.get("fuente") or f.get("doc_id"), "doc_id": f.get("doc_id"), "chunk_id": f["chunk_id"],
             "tipo": f["tipo"], "estado": f.get("estado"),
             "fragmento": f["texto"] if len(f["texto"]) <= MAX_CARACTERES else f["texto"][:MAX_CARACTERES].rsplit(" ", 1)[0] + "..."}
            for n, f in enumerate(resultado.get("fragmentos") or [], start=1)]
