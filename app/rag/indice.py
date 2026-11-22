"""Índice vectorial en PostgreSQL + pgvector, indexación seudonimizada y búsqueda con pre-filtro clínico.

Tabla rag_chunks: una fila por fragmento, con sus metadatos (tipo, estado, paciente) y el
nombre del modelo de embeddings con que se generó el vector. Los filtros clínicos son un
WHERE de SQL que se aplica ANTES de rankear.
"""
import asyncio
import logging

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.ai_report import AIReport, MLPayload
from app.models.encounter import Encounter
from app.models.observation import Observation
from app.models.patient import Patient
from app.rag.motor import (SIN_EVIDENCIA, cargar_guias, construir_mensajes, fragmentar, generar,
                           ranking_bm25, rrf)
from app.rag.seudonimizar import fugas, identificadores_paciente, prueba_canario, seudonimizar
from app.services.ml_client import embed
from app.services.pseudonym import patient_pseudonym

log = logging.getLogger("rag")

ESTADO_REPORTE = {"PENDING": "pendiente", "APPROVED": "aprobado", "REJECTED": "rechazado"}
NIVEL = {"CRITICAL": "crítico", "HIGH": "alto", "LOW": "bajo"}
_MODELO = {"nombre": None, "dim": None}


class FugaDatosPersonales(RuntimeError):
    pass


# ---------- Embeddings ----------
def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"  # formato de texto que acepta pgvector


async def embeber(textos: list[str]) -> list[list[float]]:
    vectores = []
    for i in range(0, len(textos), 64):
        r = await embed(textos[i:i + 64])
        _MODELO.update(nombre=r["modelo"], dim=r["dim"])
        vectores += r["vectores"]
    return vectores


async def modelo_actual() -> tuple[str, int]:
    if not _MODELO["nombre"]:
        await embeber(["insuficiencia cardiaca"])
    return _MODELO["nombre"], _MODELO["dim"]


# ---------- Esquema ----------
def asegurar_esquema(db: Session, dim: int, modelo: str) -> None:
    db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    dim_actual = db.scalar(text(
        "SELECT atttypmod FROM pg_attribute WHERE attrelid = to_regclass('rag_chunks') AND attname = 'embedding'"))
    if dim_actual is not None and int(dim_actual) != int(dim):
        # Otro modelo con otra dimensión: los vectores viejos no sirven, se recrea la tabla.
        log.warning("RAG: dimensión %s != %s, se recrea rag_chunks", dim_actual, dim)
        db.execute(text("DROP TABLE rag_chunks"))
    db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS rag_chunks (
            chunk_id    TEXT PRIMARY KEY,
            doc_id      TEXT NOT NULL,
            tipo        TEXT NOT NULL,          -- guia | norma | historia
            estado      TEXT NOT NULL,          -- vigente | aprobado | pendiente | rechazado | eliminado
            paciente    TEXT,                   -- seudónimo PSN-..., nunca el id real
            fuente      TEXT NOT NULL,
            texto       TEXT NOT NULL,
            modelo      TEXT NOT NULL,          -- modelo de embeddings que generó el vector
            embedding   vector({int(dim)}) NOT NULL,
            actualizado TIMESTAMP NOT NULL DEFAULT now()
        )"""))
    db.execute(text("CREATE INDEX IF NOT EXISTS rag_chunks_hnsw ON rag_chunks USING hnsw (embedding vector_cosine_ops)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS rag_chunks_filtro ON rag_chunks (tipo, estado, paciente)"))
    # Vectores de otro modelo (misma dimensión) tampoco son comparables: se borran y se re-indexan.
    db.execute(text("DELETE FROM rag_chunks WHERE modelo <> :m"), {"m": modelo})
    db.commit()


# ---------- Sincronización ----------
async def _sincronizar(db: Session, chunks: list[dict], where: str, params: dict) -> dict:
    """Inserta/actualiza solo los fragmentos que cambiaron y borra los que ya no existen."""
    modelo, _ = await modelo_actual()
    existentes = dict(db.execute(text(f"SELECT chunk_id, texto FROM rag_chunks WHERE {where}"), params).all())
    nuevos = {c["chunk_id"]: c for c in chunks}
    borrar = [cid for cid in existentes if cid not in nuevos]
    cambiar = [c for cid, c in nuevos.items() if existentes.get(cid) != c["texto"]]
    if borrar:
        db.execute(text("DELETE FROM rag_chunks WHERE chunk_id = ANY(CAST(:ids AS text[]))"), {"ids": borrar})
    if cambiar:
        vectores = await embeber([c["texto"] for c in cambiar])
        for c, v in zip(cambiar, vectores):
            db.execute(text("""
                INSERT INTO rag_chunks (chunk_id, doc_id, tipo, estado, paciente, fuente, texto, modelo, embedding, actualizado)
                VALUES (:chunk_id, :doc_id, :tipo, :estado, :paciente, :fuente, :texto, :modelo, CAST(:emb AS vector), now())
                ON CONFLICT (chunk_id) DO UPDATE SET doc_id = EXCLUDED.doc_id, tipo = EXCLUDED.tipo,
                    estado = EXCLUDED.estado, paciente = EXCLUDED.paciente, fuente = EXCLUDED.fuente,
                    texto = EXCLUDED.texto, modelo = EXCLUDED.modelo, embedding = EXCLUDED.embedding, actualizado = now()
            """), {**{k: c[k] for k in ("chunk_id", "doc_id", "tipo", "estado", "paciente", "fuente", "texto")},
                   "modelo": modelo, "emb": _vec(v)})
    db.commit()
    return {"fragmentos": len(nuevos), "actualizados": len(cambiar), "borrados": len(borrar)}


async def indexar_guias(db: Session) -> dict:
    return await _sincronizar(db, fragmentar(cargar_guias()), "tipo IN ('guia', 'norma')", {})


# ---------- Historia del paciente (seudonimizada antes de indexar) ----------
def _fecha(d) -> str:
    return d.strftime("%Y-%m-%d") if d else "sin fecha"


def _num(x) -> str:
    if x is None:
        return "sin dato"
    f = float(x)
    return str(int(f)) if f.is_integer() else f"{f:g}".replace(".", ",")


def _si_no(x) -> str:
    return "sí" if int(x or 0) == 1 else "no"


def documentos_paciente(db: Session, patient: Patient) -> list[dict]:
    """Construye la historia con campos clínicos estructurados y limpia el texto libre.

    Se incluyen reportes pendientes y rechazados con su estado: el filtro clínico es el que
    impide que lleguen a las fuentes. Los registros con soft delete quedan como 'eliminado'.
    """
    alias = patient_pseudonym(str(patient.id))
    ids = identificadores_paciente(patient)
    limpiar = lambda t: seudonimizar(t, ids, alias)  # noqa: E731
    docs = []

    if patient.primary_diagnosis:
        docs.append({"id": f"P-{alias[4:]}", "tipo": "historia", "estado": "aprobado", "paciente": alias,
                     "fuente": "Ficha del paciente · diagnóstico principal",
                     "texto": f"Paciente {alias}. Diagnóstico principal registrado: {limpiar(patient.primary_diagnosis)}."})

    filas = db.execute(select(AIReport, MLPayload).outerjoin(MLPayload, MLPayload.report_id == AIReport.id)
                       .where(AIReport.patient_id == patient.id).order_by(AIReport.created_at)).all()
    for rep, pay in filas:
        estado = ESTADO_REPORTE.get(rep.status, rep.status.lower())
        p = (pay.payload if pay else {}) or {}
        criterios = (rep.result or {}).get("criterios_criticos") or []
        texto = (f"Paciente {alias}. Reporte de IA del {_fecha(rep.created_at)} ({estado}). "
                 f"Riesgo estimado {rep.risk_score:.0%}, nivel {NIVEL.get(str(rep.level).upper(), rep.level)}. "
                 f"Criterios críticos: {', '.join(criterios) if criterios else 'ninguno'}. "
                 f"Datos de entrada: edad {_num(p.get('age'))} años, FEVI {_num(p.get('ejection_fraction'))} %, "
                 f"creatinina {_num(p.get('serum_creatinine'))} mg/dL, sodio {_num(p.get('serum_sodium'))} mEq/L, "
                 f"plaquetas {_num(p.get('platelets'))}, CPK {_num(p.get('creatinine_phosphokinase'))}, "
                 f"anemia {_si_no(p.get('anaemia'))}, diabetes {_si_no(p.get('diabetes'))}, "
                 f"hipertensión {_si_no(p.get('high_blood_pressure'))}, fumador {_si_no(p.get('smoking'))}, "
                 f"seguimiento {_num(p.get('time'))} días.")
        if rep.clinical_notes:
            texto += f" Observación del clínico: {limpiar(rep.clinical_notes)}"
        docs.append({"id": f"R-{rep.id.hex[:12]}", "tipo": "historia", "estado": estado, "paciente": alias,
                     "fuente": f"Reporte IA {estado} · {_fecha(rep.created_at)}", "texto": texto})

    for enc in db.scalars(select(Encounter).where(Encounter.patient_id == patient.id).order_by(Encounter.start_at)):
        estado = "aprobado" if enc.is_active and enc.deleted_at is None else "eliminado"
        texto = f"Paciente {alias}. Consulta {enc.encounter_type.lower()} del {_fecha(enc.start_at)}."
        if enc.reason:
            texto += f" Motivo: {limpiar(enc.reason)}."
        if enc.clinical_notes:
            texto += f" Notas clínicas: {limpiar(enc.clinical_notes)}"
        docs.append({"id": f"E-{enc.id.hex[:12]}", "tipo": "historia", "estado": estado, "paciente": alias,
                     "fuente": f"Consulta · {_fecha(enc.start_at)}", "texto": texto})

    for obs in db.scalars(select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.measured_at)):
        estado = "aprobado" if obs.is_active and obs.deleted_at is None else "eliminado"
        if obs.value_type == "COMPONENTS" and obs.components:
            valor = "; ".join(f"{c.display or c.code} {_num(c.value_numeric)} {c.unit or ''}".strip() for c in obs.components)
        elif obs.value_numeric is not None:
            valor = f"{_num(obs.value_numeric)} {obs.unit or ''}".strip()
        elif obs.value_boolean is not None:
            valor = _si_no(obs.value_boolean)
        else:
            valor = limpiar(obs.value_text) or "sin valor"
        nombre = obs.display or obs.code or "observación"
        docs.append({"id": f"O-{obs.id.hex[:12]}", "tipo": "historia", "estado": estado, "paciente": alias,
                     "fuente": f"Observación · {_fecha(obs.measured_at)}",
                     "texto": f"Paciente {alias}. Observación del {_fecha(obs.measured_at)}: {nombre} ({obs.code or 'sin código'}) = {valor}."})
    return docs


async def indexar_paciente(db: Session, patient: Patient) -> dict:
    alias = patient_pseudonym(str(patient.id))
    chunks = fragmentar(documentos_paciente(db, patient))
    restos = fugas([c["texto"] for c in chunks], identificadores_paciente(patient))
    if restos:  # prueba canario sobre datos reales: si algo sobrevive, no se indexa nada
        raise FugaDatosPersonales(f"{len(restos)} identificador(es) sobrevivieron a la seudonimización")
    return await _sincronizar(db, chunks, "tipo = 'historia' AND paciente = :alias", {"alias": alias})


async def reindexar_todo(db: Session) -> dict:
    modelo, dim = await modelo_actual()
    asegurar_esquema(db, dim, modelo)
    guias = await indexar_guias(db)
    pacientes, bloqueados = 0, []
    for patient in db.scalars(select(Patient).where(Patient.is_active.is_(True), Patient.deleted_at.is_(None))):
        try:
            await indexar_paciente(db, patient)
            pacientes += 1
        except FugaDatosPersonales:
            db.rollback()
            bloqueados.append(patient.patient_code)
    # Los pacientes con soft delete salen del índice.
    activos = [patient_pseudonym(str(i)) for i in db.scalars(
        select(Patient.id).where(Patient.is_active.is_(True), Patient.deleted_at.is_(None)))]
    db.execute(text("DELETE FROM rag_chunks WHERE tipo = 'historia' AND NOT (paciente = ANY(CAST(:a AS text[])))"), {"a": activos})
    db.commit()
    return {"modelo": modelo, "dim": dim, "guias": guias, "pacientes_indexados": pacientes,
            "pacientes_bloqueados_por_fuga": bloqueados, "canario": prueba_canario()["ok"]}


# ---------- Búsqueda con pre-filtro ----------
def _filtro(alias: str | None, solo_guias: bool = False) -> tuple[str, dict]:
    if solo_guias or alias is None:
        return "modelo = :modelo AND tipo IN ('guia', 'norma')", {}
    return ("modelo = :modelo AND (tipo IN ('guia', 'norma') "
            "OR (tipo = 'historia' AND estado = 'aprobado' AND paciente = :alias))"), {"alias": alias}


def chunks_permitidos(db: Session, modelo: str, alias: str | None, solo_guias: bool = False) -> list[dict]:
    where, params = _filtro(alias, solo_guias)
    filas = db.execute(text(f"SELECT chunk_id, doc_id, tipo, estado, paciente, fuente, texto FROM rag_chunks WHERE {where} "
                            "ORDER BY chunk_id"), {"modelo": modelo, **params}).mappings().all()
    return [dict(f) for f in filas]


def ranking_denso(db: Session, vector: list[float], modelo: str, alias: str | None,
                  solo_guias: bool = False, limite: int = 200) -> list[tuple[str, float]]:
    where, params = _filtro(alias, solo_guias)
    # ef_search alto: con el filtro en el WHERE, HNSW debe explorar suficientes vecinos.
    db.execute(text("SET LOCAL hnsw.ef_search = 400"))
    filas = db.execute(text(f"""
        SELECT chunk_id, 1 - (embedding <=> CAST(:q AS vector)) AS similitud
        FROM rag_chunks WHERE {where}
        ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"""),
        {"q": _vec(vector), "modelo": modelo, "k": limite, **params}).all()
    db.commit()
    return [(cid, float(sim)) for cid, sim in filas]


def _permitido(c: dict, alias: str | None) -> bool:
    if c["tipo"] in ("guia", "norma"):
        return True
    return c["tipo"] == "historia" and c["estado"] == "aprobado" and alias is not None and c["paciente"] == alias


async def recuperar(db: Session, pregunta: str, alias: str | None, metodo: str, k: int) -> dict:
    """Solo recuperación (sin generar): la usa /rag/preguntar y la herramienta buscar_guias del agente."""
    modelo, _ = await modelo_actual()
    permitidos = chunks_permitidos(db, modelo, alias)            # 1. pre-filtro clínico (SQL)
    por_id = {c["chunk_id"]: c for c in permitidos}
    lexico, max_bm25 = ranking_bm25(permitidos, pregunta)          # 2. ranking solo sobre lo permitido
    denso = ranking_denso(db, (await embeber([pregunta]))[0], modelo, alias, limite=max(len(permitidos), 1))
    max_cos = denso[0][1] if denso else 0.0
    base = {"metodo": metodo, "puntajes": {"max_coseno": round(max_cos, 3), "max_bm25": round(max_bm25, 3)}}

    if not permitidos or (max_cos < settings.rag_umbral_cos and max_bm25 < settings.rag_umbral_bm25):
        return {**base, "abstencion": True, "fragmentos": []}

    listas = {"bm25": lexico, "denso": [cid for cid, _ in denso], "hibrido": rrf([lexico, [cid for cid, _ in denso]])}
    elegidos = [por_id[cid] for cid in listas[metodo] if cid in por_id][:k]
    # Defensa en profundidad: aunque el WHERE ya filtró, ninguna fuente no permitida puede salir.
    if any(not _permitido(c, alias) for c in elegidos):
        raise RuntimeError("Fuente no permitida en el RAG")
    return {**base, "abstencion": False, "fragmentos": elegidos}


async def responder(db: Session, pregunta: str, alias: str | None, metodo: str, k: int) -> dict:
    r = await recuperar(db, pregunta, alias, metodo, k)
    base = {"metodo": r["metodo"], "puntajes": r["puntajes"]}
    if r["abstencion"]:
        return {**base, "respuesta": SIN_EVIDENCIA, "modo": "abstencion", "abstencion": True, "fuentes": [], "mensajes": []}
    elegidos = r["fragmentos"]
    mensajes = construir_mensajes(pregunta, elegidos)                # 3. prompt aumentado
    texto, modo = await generar(mensajes, elegidos, settings.groq_api_key, settings.groq_model)
    fuentes = [{"n": n, "chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "tipo": c["tipo"], "estado": c["estado"],
                "paciente": c["paciente"], "fuente": c["fuente"]} for n, c in enumerate(elegidos, 1)]
    return {**base, "respuesta": texto, "modo": modo, "abstencion": SIN_EVIDENCIA in texto,
            "fuentes": fuentes, "mensajes": mensajes}


# ---------- Arranque ----------
async def iniciar_rag() -> None:
    """Crea la tabla y carga las guías en segundo plano (el servicio ML puede tardar en cargar el modelo)."""
    canario = prueba_canario()
    if not canario["ok"]:
        log.error("RAG: la prueba canario falló, no se indexa: %s", canario["fugas"])
        return
    for intento in range(60):
        try:
            modelo, dim = await modelo_actual()
            with SessionLocal() as db:
                asegurar_esquema(db, dim, modelo)
                r = await indexar_guias(db)
            log.info("RAG listo: modelo=%s dim=%s guias=%s", modelo, dim, r)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("RAG: intento %s sin éxito (%s); reintento en 5 s", intento + 1, exc)
            await asyncio.sleep(5)
