"""Seudonimización ANTES de indexar y prueba canario.

Regla: lo que no debe salir del sistema tampoco entra al índice. Los
fragmentos del paciente se construyen con campos clínicos estructurados y el
texto libre (notas del médico) se limpia con los identificadores reales que
vienen de la base, más patrones de documento, teléfono y correo.
"""
import re
from functools import lru_cache

from app.rag.motor import cargar_guias, sin_tildes, tokenizar

_VARIANTES = {"a": "[aáàä]", "e": "[eéèë]", "i": "[iíìï]", "o": "[oóòö]", "u": "[uúùü]", "n": "[nñ]"}

PATRONES_PII = [
    (re.compile(r"\b(?:c\.?\s?c\.?|c[eé]dula|documento|t\.?\s?i\.?|c\.?\s?e\.?)\s*(?:n[o°º]\.?\s*)?:?\s*\d[\d\.\s]{4,}\d\b", re.I), "[DOCUMENTO]"),
    (re.compile(r"(?:\+?57\s*)?\b3\d{2}[\s-]?\d{3}[\s-]?\d{4}\b"), "[TELEFONO]"),
    (re.compile(r"[\w\.-]+@[\w\.-]+\.\w+"), "[CORREO]"),
]


def _cuerpo_flexible(valor: str) -> str:
    base = sin_tildes(valor.lower().strip())
    cuerpo = "".join(_VARIANTES.get(ch, re.escape(ch)) for ch in base)
    return cuerpo.replace(r"\ ", r"\s+")


def _patron_flexible(valor: str) -> re.Pattern:
    """Coincide sin importar mayúsculas ni tildes: 'maria' encuentra 'María'."""
    return re.compile(rf"(?<!\w){_cuerpo_flexible(valor)}(?!\w)", re.I)


@lru_cache(maxsize=1)
def vocabulario_clinico() -> frozenset:
    """Palabras del corpus de guías ("paciente", "cardiaco"...). Una sola de ellas no identifica a nadie."""
    return frozenset(t for d in cargar_guias() for t in tokenizar(d["texto"]))


def _comun(valor: str) -> bool:
    palabras = valor.split()
    return len(palabras) == 1 and sin_tildes(valor.lower()) in vocabulario_clinico()


def identificadores_paciente(patient) -> list[str]:
    """Identificadores directos del paciente tomados de la base (fuente de verdad).

    El nombre completo, el documento y el código siempre se buscan. Una parte suelta del nombre
    que también es palabra clínica (p. ej. el paciente demo "Paciente Académico Cardiaco") no se
    busca sola, porque borraría vocabulario médico y daría falsas fugas; la frase completa sí.
    """
    nombre = f"{patient.first_name or ''} {patient.last_name or ''}".strip()
    partes = [p for p in nombre.replace("-", " ").split() if len(p) >= 3 and not _comun(p)]
    # El id de HAPI no se incluye: es numérico y podría borrar valores clínicos (p. ej. sodio 126).
    valores = [nombre, patient.first_name, patient.last_name, *partes,
               patient.document_number, patient.patient_code,
               getattr(patient, "phone", None), getattr(patient, "address", None)]  # R27: también los campos cifrados
    unicos = {v.strip() for v in valores if v and len(v.strip()) >= 3 and not _comun(v.strip())}
    return sorted(unicos, key=len, reverse=True)  # primero los más largos (nombre completo)


def seudonimizar(texto: str | None, identificadores: list[str], alias: str) -> str:
    if not texto:
        return ""
    if identificadores:
        # Una sola pasada con alternación (los más largos primero): el seudónimo insertado
        # nunca se vuelve a reemplazar.
        alternativas = "|".join(_cuerpo_flexible(v) for v in sorted(identificadores, key=len, reverse=True))
        texto = re.sub(rf"(?<!\w)(?:{alternativas})(?!\w)", alias, texto, flags=re.I)
        if alias:  # "María Ruiz" -> "PSN-X PSN-X" -> "PSN-X"
            texto = re.sub(rf"{re.escape(alias)}(?:\s+{re.escape(alias)})+", alias, texto)
    for patron, reemplazo in PATRONES_PII:
        texto = patron.sub(reemplazo, texto)
    return texto


def fugas(textos: list[str], identificadores: list[str]) -> list[str]:
    """Identificadores que sobrevivieron en algún texto (debe ser una lista vacía)."""
    encontrados = set()
    for valor in identificadores:
        patron = _patron_flexible(valor)
        if any(patron.search(t or "") for t in textos):
            encontrados.add(valor)
    return sorted(encontrados)


def prueba_canario() -> dict:
    """Prueba automática con un paciente ficticio cuyos datos no pueden sobrevivir a la limpieza.

    Se ejecuta al iniciar la API, en cada reindexación completa y desde GET /rag/canario.
    """
    class _Canario:
        first_name = "Canario Tomás"
        last_name = "Piñeros Ávila"
        document_number = "9988776655"
        patient_code = "HF-CANARIO-01"

    ids = identificadores_paciente(_Canario)
    nota = ("Paciente CANARIO TOMAS PIÑEROS AVILA, CC 9.988.776.655, celular 300 123 4567, "
            "correo canario.tomas@correo.co, código HF-CANARIO-01. Tomás refiere disnea de esfuerzo; "
            "plaquetas 250000, sodio 126 mEq/L.")
    limpio = seudonimizar(nota, ids, "PSN-000000000000")
    restos = fugas([limpio], ids) + [x for x in ("9.988.776.655", "300 123 4567", "canario.tomas@correo.co") if x in limpio]
    clinico_intacto = "250000" in limpio and "126" in limpio
    return {"ok": not restos and clinico_intacto, "fugas": restos, "clinico_intacto": clinico_intacto, "texto_limpio": limpio}
