# app/agente/herramientas.py · Lo que el agente puede hacer (y nada más).
#
# Reglas del notebook de la Semana 13:
#  - El paciente, el usuario y el rol NO son argumentos: los fija el harness en ContextoClinico y llegan
#    por `runtime`, que LangChain oculta al modelo. El modelo no puede pedir otro paciente.
#  - Minimización (R12): ninguna herramienta devuelve nombre, documento, teléfono ni código del paciente.
#  - Los errores se devuelven como texto para que el modelo pueda avisar.
#  - Lectura y escritura separadas: proponer_remision pasa por aprobación humana (HumanInTheLoop).
import uuid
from dataclasses import dataclass
from datetime import date

from langchain.tools import ToolRuntime, tool
from sqlalchemy import select

from app.database import SessionLocal
from app.models.ai_report import AIReport, MLPayload
from app.models.alert import Alert
from app.models.observation import Observation
from app.models.patient import Patient
from app.models.user import User
from app.rag.seudonimizar import identificadores_paciente, seudonimizar
from app.services.access import user_can_access_patient

ROLES_CLINICOS = {"medico", "especialista"}
NIVEL = {"CRITICAL": "crítico", "HIGH": "alto", "LOW": "bajo"}
ESTADO = {"PENDING": "pendiente", "APPROVED": "aprobado", "REJECTED": "rechazado"}


# ---------- Contexto que fija el harness en cada solicitud ----------
@dataclass
class ContextoClinico:
    usuario: str                       # username del token
    usuario_id: str                    # UUID del usuario (para auditoría y permisos)
    rol: str                           # canónico: medico, especialista, ...
    conversacion: str = "principal"
    paciente_id: str | None = None     # UUID REAL: el modelo nunca lo ve
    paciente_alias: str | None = None  # seudónimo PSN-...: lo único que circula hacia el modelo
    ip: str | None = None              # R25: para registrar y bloquear intentos de manipulación


def espacio_memoria(usuario: str) -> tuple:
    """Namespace del store. LangGraph no admite puntos en el namespace (medico.demo -> medico_demo)."""
    return ("preferencias", usuario.replace(".", "_").replace("@", "_at_"))


def _num(x) -> str:
    if x is None:
        return "sin dato"
    f = float(x)
    return str(int(f)) if f.is_integer() else f"{f:g}".replace(".", ",")


def _abrir_paciente(db, ctx: ContextoClinico) -> tuple[Patient | None, str | None]:
    """Valida rol, paciente abierto y asignación (defensa en profundidad: el endpoint ya lo revisó)."""
    if ctx.rol not in ROLES_CLINICOS:
        return None, "NO_AUTORIZADO: solo personal clínico consulta datos de pacientes."
    if ctx.paciente_id is None:
        return None, "SIN_PACIENTE: no hay un paciente abierto en esta conversación."
    patient = db.get(Patient, uuid.UUID(ctx.paciente_id))
    if patient is None or not patient.is_active or patient.deleted_at is not None:
        return None, "NO_ENCONTRADO: el paciente no existe o está inactivo."
    user = db.get(User, uuid.UUID(ctx.usuario_id))
    if user is None or not user_can_access_patient(db, user=user, patient_id=patient.id):
        return None, "NO_AUTORIZADO: el usuario no tiene asignado este paciente."
    return patient, None


# ---------- Herramienta 1 · lectura: datos clínicos mínimos ----------
FUENTE_HISTORIA = "Historia clínica aprobada del paciente (ficha, observaciones y último reporte de IA aprobado)"
FUENTE_REPORTES = "Reportes de IA aprobados y alertas activas del paciente"


def _con_fuente(texto: str, documento: str, tipo: str = "historia") -> tuple[str, list]:
    """R23: el texto va al modelo; la fuente (documento + fragmento) va como artefacto al harness, no al modelo."""
    if texto.startswith(("NO_", "SIN_")):
        return texto, []
    return texto, [{"documento": documento, "tipo": tipo, "fragmento": texto[:350]}]


@tool(response_format="content_and_artifact")
def consultar_paciente(runtime: ToolRuntime[ContextoClinico]) -> tuple[str, list]:
    """Devuelve los datos clínicos del paciente abierto: sexo, edad, diagnóstico principal y sus últimas
    observaciones (signos vitales, laboratorios). Úsala cuando la pregunta se refiera a "este paciente".
    No recibe argumentos: el paciente lo define el sistema."""
    ctx = runtime.context
    with SessionLocal() as db:
        patient, error = _abrir_paciente(db, ctx)
        if error:
            return error, []
        ids = identificadores_paciente(patient)
        edad = None
        if patient.birth_date:
            hoy = date.today()
            edad = hoy.year - patient.birth_date.year - ((hoy.month, hoy.day) < (patient.birth_date.month, patient.birth_date.day))
        partes = [f"Paciente {ctx.paciente_alias}", f"sexo {patient.sex or 'sin dato'}",
                  f"edad {edad if edad is not None else 'sin dato'} años"]
        if patient.primary_diagnosis:
            partes.append("diagnóstico principal: " + seudonimizar(patient.primary_diagnosis, ids, ctx.paciente_alias))
        vistos, obs = set(), []
        for o in db.scalars(select(Observation).where(Observation.patient_id == patient.id, Observation.is_active.is_(True),
                                                      Observation.deleted_at.is_(None)).order_by(Observation.measured_at.desc())):
            clave = o.code or o.display
            if clave in vistos:
                continue
            vistos.add(clave)
            if o.value_type == "COMPONENTS" and o.components:
                valor = "/".join(_num(c.value_numeric) for c in o.components) + f" {o.unit or o.components[0].unit or ''}"
            elif o.value_numeric is not None:
                valor = f"{_num(o.value_numeric)} {o.unit or ''}"
            elif o.value_boolean is not None:
                valor = "sí" if o.value_boolean else "no"
            else:
                valor = seudonimizar(o.value_text, ids, ctx.paciente_alias) or "sin valor"
            fecha = o.measured_at.strftime("%Y-%m-%d") if o.measured_at else "sin fecha"
            obs.append(f"{o.display or o.code}: {valor.strip()} ({fecha})")
            if len(obs) == 10:
                break
        texto = ", ".join(partes) + "."
        texto += (" Últimas observaciones: " + "; ".join(obs) + ".") if obs else " Sin observaciones registradas."
        # Variables del último reporte de IA APROBADO (FEVI, creatinina, sodio son datos clínicos del paciente).
        ultimo = db.execute(select(AIReport, MLPayload).outerjoin(MLPayload, MLPayload.report_id == AIReport.id)
                            .where(AIReport.patient_id == patient.id, AIReport.status == "APPROVED")
                            .order_by(AIReport.created_at.desc()).limit(1)).first()
        if ultimo:
            r, p = ultimo
            d = (p.payload if p else {}) or {}
            texto += (f" Último reporte de IA aprobado ({r.created_at:%Y-%m-%d}): FEVI {_num(d.get('ejection_fraction'))} %, "
                      f"creatinina {_num(d.get('serum_creatinine'))} mg/dL, sodio {_num(d.get('serum_sodium'))} mEq/L, "
                      f"edad {_num(d.get('age'))} años, riesgo {r.risk_score:.0%} (nivel {NIVEL.get(str(r.level).upper(), r.level)}).")
        return _con_fuente(texto, FUENTE_HISTORIA)


# ---------- Herramienta 2 · lectura: el RAG de la Semana 12 ----------
@tool(response_format="content_and_artifact")
async def buscar_guias(pregunta: str, runtime: ToolRuntime[ContextoClinico]) -> tuple[str, list]:
    """Busca en las guías de insuficiencia cardiaca del equipo, en la normativa y en la historia APROBADA del
    paciente abierto. Úsala para criterios, metas, tratamiento, signos de alarma, remisión o normas.
    Escribe una pregunta concreta (por ejemplo "criterios de remisión a cardiología con FEVI baja").
    Devuelve fragmentos numerados [1], [2]...; cita esos números. Si devuelve SIN_EVIDENCIA, dilo y no inventes."""
    from app.agente.rag_conector import consultar_rag, formatear_para_agente, fuentes_de
    ctx = runtime.context
    if ctx.rol not in ROLES_CLINICOS:
        return "NO_AUTORIZADO: solo personal clínico consulta el RAG.", []
    resultado = await consultar_rag(pregunta, ctx.paciente_alias, ctx.paciente_id)
    return formatear_para_agente(resultado), fuentes_de(resultado)


# ---------- Herramienta 3 · lectura: reportes de IA y alertas ----------
@tool(response_format="content_and_artifact")
def consultar_reportes_ia(runtime: ToolRuntime[ContextoClinico]) -> tuple[str, list]:
    """Devuelve los últimos reportes del modelo de riesgo (IA) APROBADOS del paciente abierto, con su riesgo,
    nivel, criterios críticos y variables de entrada, y sus alertas activas. Los reportes pendientes o
    rechazados no se muestran (R10): solo se informa cuántos hay. No recibe argumentos."""
    ctx = runtime.context
    with SessionLocal() as db:
        patient, error = _abrir_paciente(db, ctx)
        if error:
            return error, []
        filas = db.execute(select(AIReport, MLPayload).outerjoin(MLPayload, MLPayload.report_id == AIReport.id)
                           .where(AIReport.patient_id == patient.id).order_by(AIReport.created_at.desc())).all()
        aprobados = [(r, p) for r, p in filas if r.status == "APPROVED"][:3]
        otros = {}
        for r, _ in filas:
            if r.status != "APPROVED":
                otros[ESTADO.get(r.status, r.status)] = otros.get(ESTADO.get(r.status, r.status), 0) + 1
        lineas = []
        for r, p in aprobados:
            d = (p.payload if p else {}) or {}
            criterios = (r.result or {}).get("criterios_criticos") or []
            lineas.append(
                f"- {r.created_at:%Y-%m-%d}: riesgo {r.risk_score:.0%}, nivel {NIVEL.get(str(r.level).upper(), r.level)}, "
                f"criterios críticos: {', '.join(criterios) or 'ninguno'}; FEVI {_num(d.get('ejection_fraction'))} %, "
                f"creatinina {_num(d.get('serum_creatinine'))} mg/dL, sodio {_num(d.get('serum_sodium'))} mEq/L, "
                f"edad {_num(d.get('age'))}, seguimiento {_num(d.get('time'))} días.")
        alertas = list(db.scalars(select(Alert).where(Alert.patient_id == patient.id, Alert.state == "ACTIVE")
                                  .order_by(Alert.created_at.desc())))
        texto = f"Paciente {ctx.paciente_alias}. "
        texto += ("Reportes de IA aprobados:\n" + "\n".join(lineas)) if lineas else "Sin reportes de IA aprobados."
        if otros:
            texto += "\nReportes sin aprobar (no se muestran): " + ", ".join(f"{n} {e}" for e, n in otros.items()) + "."
        texto += (f"\nAlertas activas: {len(alertas)} (la más reciente, nivel {alertas[0].level}, del "
                  f"{alertas[0].created_at:%Y-%m-%d})." if alertas else "\nSin alertas activas.")
        return _con_fuente(texto, FUENTE_REPORTES)


# ---------- Herramienta 4 · escritura (requiere aprobación humana) ----------
@tool
async def proponer_remision(especialidad: str, motivo: str, runtime: ToolRuntime[ContextoClinico]) -> str:
    """Remite al paciente abierto a cardiología o a una unidad de insuficiencia cardiaca.
    Llamar a esta herramienta ES proponer la remisión: el sistema la pausa y le muestra al médico los botones
    Aprobar y Rechazar. Escribir la propuesta en texto NO la muestra ni crea nada. Si el médico pide remitir y hay
    un criterio que la respalde, llámala en este turno con un motivo que cite el criterio [n]."""
    from fastapi import HTTPException
    from app.routers.referrals import registrar_remision
    ctx = runtime.context
    if ctx.rol != "medico":
        return "NO_AUTORIZADO: solo el médico crea remisiones (R08)."
    with SessionLocal() as db:
        patient, error = _abrir_paciente(db, ctx)
        if error:
            return error
        motivo_limpio = seudonimizar(motivo, identificadores_paciente(patient), ctx.paciente_alias)[:400]
        user = db.get(User, uuid.UUID(ctx.usuario_id))
        try:
            # La MISMA función del endpoint del Corte 2: notifica al especialista, audita y refleja en FHIR.
            r = await registrar_remision(db, patient=patient, current_user=user, origen="agente",
                                         reason=f"[{especialidad}] {motivo_limpio} (propuesta por el agente, aprobada por el médico)")
        except HTTPException as exc:
            return f"ERROR_REMISION: {exc.detail}"
    return f"REMISION_CREADA id={r['id'][:8]} paciente={ctx.paciente_alias} especialidad={especialidad} estado=pendiente"


# ---------- Herramienta 4b · ejecutar el modelo de riesgo (R24): el resultado es un BORRADOR pendiente ----------
LOINC_A_VARIABLE = {"2160-0": ("serum_creatinine", 1.0), "2951-2": ("serum_sodium", 1.0), "10230-1": ("ejection_fraction", 1.0),
                    "2157-6": ("creatinine_phosphokinase", 1.0), "777-3": ("platelets", 1000.0)}


@tool
async def ejecutar_modelo_riesgo(runtime: ToolRuntime[ContextoClinico]) -> str:
    """Ejecuta el modelo de riesgo de descompensación con las variables más recientes del paciente abierto
    (las del último análisis, actualizadas con los laboratorios LOINC más nuevos: creatinina, sodio, FEVI, CPK y
    plaquetas). El resultado queda como reporte de IA PENDIENTE: no es válido hasta que un clínico lo apruebe.
    Úsala cuando el clínico pida recalcular o estimar el riesgo. No recibe argumentos."""
    from fastapi import HTTPException
    from app.routers.ai import analyze
    from app.schemas.corte2 import AIAnalyzeRequest
    ctx = runtime.context
    with SessionLocal() as db:
        patient, error = _abrir_paciente(db, ctx)
        if error:
            return error
        previo = db.execute(select(MLPayload).join(AIReport, AIReport.id == MLPayload.report_id)
                            .where(AIReport.patient_id == patient.id).order_by(AIReport.created_at.desc()).limit(1)).scalar()
        if previo is None:
            return ("FALTAN_DATOS: el paciente no tiene un análisis previo con las 12 variables (antecedentes como anemia, "
                    "diabetes o tabaquismo no se pueden deducir). Pida al clínico hacer el primer análisis desde la ficha.")
        variables = {k: v for k, v in (previo.payload or {}).items() if k in AIAnalyzeRequest.model_fields and k != "image_id"}
        actualizadas = []
        for codigo, (campo, factor) in LOINC_A_VARIABLE.items():
            o = db.scalars(select(Observation).where(Observation.patient_id == patient.id, Observation.code == codigo,
                                                     Observation.is_active.is_(True), Observation.deleted_at.is_(None),
                                                     Observation.value_numeric.is_not(None))
                           .order_by(Observation.measured_at.desc()).limit(1)).first()
            if o is not None:
                variables[campo] = float(o.value_numeric) * factor
                actualizadas.append(campo)
        if patient.birth_date:
            hoy = date.today()
            variables["age"] = hoy.year - patient.birth_date.year - ((hoy.month, hoy.day) < (patient.birth_date.month, patient.birth_date.day))
        user = db.get(User, uuid.UUID(ctx.usuario_id))
        try:
            r = await analyze(patient_id=str(patient.id), payload=AIAnalyzeRequest(**variables), db=db, current_user=user)
        except HTTPException as exc:
            return f"ERROR_MODELO: {exc.detail}"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR_MODELO: {type(exc).__name__}"
    return (f"REPORTE_PENDIENTE id={r['id'][:8]} paciente={ctx.paciente_alias} riesgo={r['risk_score']:.0%} nivel={r['nivel']}. "
            f"Variables actualizadas con laboratorios recientes: {', '.join(actualizadas) or 'ninguna'}. "
            "Queda PENDIENTE de aprobación del clínico (no es un diagnóstico).")


# ---------- Herramienta 5 · skills bajo demanda ----------
@tool
def cargar_skill(nombre: str) -> str:
    """Carga el procedimiento completo de una skill de la lista 'Skills disponibles'.
    Úsala antes de una tarea que coincida con la descripción de una skill y sigue sus pasos."""
    from app.agente.skills import cargar
    return cargar(nombre)


# ---------- Herramienta 6 · memoria de largo plazo ----------
@tool
async def guardar_preferencia(preferencia: str, runtime: ToolRuntime[ContextoClinico]) -> str:
    """Guarda una preferencia de trabajo del usuario (formato, nivel de detalle, idioma).
    Úsala cuando el usuario diga "recuerda que..." o "prefiero...". Nunca guardes datos de pacientes."""
    if runtime.store is None:
        return "SIN_MEMORIA: este agente no tiene memoria de largo plazo."
    texto = seudonimizar(preferencia, [], "")[:200]          # quita documentos, teléfonos y correos
    if runtime.context.paciente_alias and runtime.context.paciente_alias in texto:
        return "RECHAZADO: las preferencias no pueden contener datos de pacientes."
    clave = "pref-" + uuid.uuid4().hex[:6]
    await runtime.store.aput(espacio_memoria(runtime.context.usuario), clave, {"texto": texto})
    return "PREFERENCIA_GUARDADA"


HERRAMIENTAS = [consultar_paciente, buscar_guias, consultar_reportes_ia, ejecutar_modelo_riesgo, proponer_remision,
                cargar_skill, guardar_preferencia]
