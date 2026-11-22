"""Endpoints del RAG clínico (Semana 12).

POST /rag/preguntar   médico y especialista: respuesta con citas, abstención y auditoría.
GET  /rag/evaluacion  admin: Recall@1, Recall@3 y MRR de BM25, denso e híbrido + calibración de umbrales.
POST /rag/reindexar   admin: reindexa guías y la historia de todos los pacientes.
GET  /rag/canario     admin: prueba canario sintética + escaneo del índice completo.
GET  /rag/estado      admin: modelo de embeddings y conteo de fragmentos por tipo y estado.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies.auth import require_roles
from app.models.patient import Patient
from app.models.user import User
from app.rag import indice
from app.rag.evaluacion import EVAL, FUERA_DE_DOMINIO, a_documentos, metricas
from app.rag.motor import ranking_bm25, rrf
from app.rag.seudonimizar import fugas, identificadores_paciente, prueba_canario, seudonimizar
from app.services.access import ensure_patient_access
from app.services.audit import add_audit
from app.services.identifiers import resolve_patient_identifier
from app.services.pseudonym import patient_pseudonym

router = APIRouter(prefix="/rag", tags=["RAG clínico"])


class PreguntaRAG(BaseModel):
    pregunta: str = Field(min_length=3, max_length=500)
    paciente_id: str | None = Field(default=None, description="UUID, documento o código del paciente (opcional)")
    metodo: Literal["bm25", "denso", "hibrido"] | None = None
    k: int = Field(default=3, ge=1, le=6)


@router.post("/preguntar")
async def preguntar(body: PreguntaRAG, db: Session = Depends(get_db),
                    current_user: User = Depends(require_roles("MEDICO", "ESPECIALISTA"))):
    metodo = body.metodo or settings.rag_metodo
    patient, alias = None, None
    if body.paciente_id:
        patient = resolve_patient_identifier(db, body.paciente_id)
        ensure_patient_access(db, user=current_user, patient_id=patient.id)   # 403 si no está asignado
        alias = patient_pseudonym(str(patient.id))
        try:
            await indice.indexar_paciente(db, patient)   # historia al día, seudonimizada antes de indexar
        except indice.FugaDatosPersonales as exc:
            db.rollback()
            add_audit(db, user_id=current_user.id, action="RAG_PII_LEAK_BLOCKED", entity_type="PATIENT",
                      entity_id=patient.id, details={"detalle": str(exc)})
            db.commit()
            raise HTTPException(status_code=500, detail="Indexación bloqueada: la prueba canario detectó datos personales")
        # Si el médico escribe el nombre o el documento en la pregunta, tampoco sale del sistema.
        pregunta = seudonimizar(body.pregunta, identificadores_paciente(patient), alias)
    else:
        pregunta = seudonimizar(body.pregunta, [], "")

    r = await indice.responder(db, pregunta, alias, metodo, body.k)

    add_audit(db, user_id=current_user.id, action="RAG_QUERY",
              entity_type="PATIENT" if patient else "RAG", entity_id=patient.id if patient else None,
              details={"pregunta": pregunta, "paciente_id": str(patient.id) if patient else None, "seudonimo": alias,
                       "metodo": metodo, "modo": r["modo"], "abstencion": r["abstencion"], "puntajes": r["puntajes"],
                       "fuentes": [f["chunk_id"] for f in r["fuentes"]],
                       "mensajes_llm": r["mensajes"]})   # el payload exacto, como el ia_payload del R12
    db.commit()
    return {"respuesta": r["respuesta"], "abstencion": r["abstencion"], "modo": r["modo"], "metodo": metodo,
            "seudonimo": alias, "fuentes": r["fuentes"], "puntajes": r["puntajes"]}


@router.get("/evaluacion")
async def evaluacion(db: Session = Depends(get_db), current_user: User = Depends(require_roles("ADMIN"))):
    modelo, _ = await indice.modelo_actual()
    guias = indice.chunks_permitidos(db, modelo, None, solo_guias=True)
    if not guias:
        raise HTTPException(status_code=503, detail="El índice de guías aún no está listo")
    doc_de = {c["chunk_id"]: c["doc_id"] for c in guias}
    preguntas = [p for p, _ in EVAL] + FUERA_DE_DOMINIO
    vectores = await indice.embeber(preguntas)

    rk = {"bm25": [], "denso": [], "hibrido": []}
    detalle, calibracion = [], {"en_dominio": [], "fuera_de_dominio": []}
    for i, pregunta in enumerate(preguntas):
        lex, max_bm25 = ranking_bm25(guias, pregunta)
        den = indice.ranking_denso(db, vectores[i], modelo, None, solo_guias=True, limite=len(guias))
        den_ids = [cid for cid, _ in den]
        punt = {"pregunta": pregunta, "max_coseno": round(den[0][1], 3) if den else 0.0, "max_bm25": round(max_bm25, 3)}
        if i >= len(EVAL):
            calibracion["fuera_de_dominio"].append(punt)
            continue
        calibracion["en_dominio"].append(punt)
        docs = {"bm25": a_documentos(lex, doc_de), "denso": a_documentos(den_ids, doc_de),
                "hibrido": a_documentos(rrf([lex, den_ids]), doc_de)}
        for m in rk:
            rk[m].append(docs[m])
        detalle.append({"pregunta": pregunta, "esperado": EVAL[i][1], **{m: docs[m][:3] for m in docs}})

    esperados = [e for _, e in EVAL]
    tabla = {"BM25": metricas(rk["bm25"], esperados), "Denso": metricas(rk["denso"], esperados),
             "Híbrido (RRF)": metricas(rk["hibrido"], esperados)}
    md = ["| Método | Recall@1 | Recall@3 | MRR |", "|---|---:|---:|---:|"]
    md += [f"| {m} | {v['recall@1']:.3f} | {v['recall@3']:.3f} | {v['mrr']:.3f} |" for m, v in tabla.items()]
    return {"modelo_embeddings": modelo, "n_preguntas": len(EVAL), "fragmentos_guia": len(guias),
            "tabla": tabla, "tabla_markdown": "\n".join(md), "metodo_configurado": settings.rag_metodo,
            "umbrales": {"coseno": settings.rag_umbral_cos, "bm25": settings.rag_umbral_bm25},
            "calibracion": calibracion, "detalle": detalle}


@router.post("/reindexar")
async def reindexar(db: Session = Depends(get_db), current_user: User = Depends(require_roles("ADMIN"))):
    r = await indice.reindexar_todo(db)
    add_audit(db, user_id=current_user.id, action="RAG_REINDEX", entity_type="RAG", entity_id=None, details=r)
    db.commit()
    return r


@router.get("/canario")
def canario(db: Session = Depends(get_db), current_user: User = Depends(require_roles("ADMIN"))):
    sintetica = prueba_canario()
    textos = [t for (t,) in db.execute(text("SELECT texto FROM rag_chunks")).all()]
    fugas_indice = {}
    for p in db.scalars(select(Patient)):
        encontrados = fugas(textos, identificadores_paciente(p))
        if encontrados:
            fugas_indice[p.patient_code] = len(encontrados)   # no se devuelven los datos, solo el conteo
    return {"ok": sintetica["ok"] and not fugas_indice, "prueba_sintetica": sintetica,
            "fragmentos_revisados": len(textos), "pacientes_con_fuga": fugas_indice}


@router.get("/estado")
def estado(db: Session = Depends(get_db), current_user: User = Depends(require_roles("ADMIN"))):
    filas = db.execute(text("SELECT modelo, tipo, estado, count(*) FROM rag_chunks GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")).all()
    return {"modelo_cargado": indice._MODELO, "metodo": settings.rag_metodo,
            "umbrales": {"coseno": settings.rag_umbral_cos, "bm25": settings.rag_umbral_bm25},
            "llm": bool(settings.groq_api_key),
            "fragmentos": [{"modelo": m, "tipo": t, "estado": e, "n": n} for m, t, e, n in filas]}
