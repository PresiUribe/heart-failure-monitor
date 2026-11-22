"""Contrato FHIR R4 de la interoperabilidad: validación del Bundle entrante y del Task, y armado de los salientes.

Todo error de contrato es un OperationOutcome con un issue por problema (R30).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from fastapi.responses import JSONResponse

from app.config import settings

FHIR_JSON = "application/fhir+json"
LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
UCUM = "http://unitsofmeasure.org"
TIPOS_BUNDLE = {"transaction", "collection", "batch", "message", "document"}
TIPOS_DOCUMENTO = {"CC", "TI", "CE", "PA", "RC", "PPT", "PE", "NIT", "MS", "AS", "CD", "SC", "DE"}
INTENTS = {"order", "original-order", "plan", "proposal", "directive"}
PRIORIDADES = {"routine", "urgent", "asap", "stat"}
OBS_STATUS = {"final", "amended", "corrected", "preliminary"}
DR_APROBADO = {"final", "amended", "corrected"}
TASK_STATUS = {"accepted", "rejected", "in-progress", "completed", "cancelled", "failed", "requested", "received"}
GENEROS = {"male": "MALE", "female": "FEMALE", "other": "OTHER", "unknown": "UNKNOWN"}
FECHA_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
# Datos de contacto que nunca guardamos ni reenviamos (R32, minimización)
CAMPOS_CONTACTO = ("telecom", "address", "contact", "photo")
ESPECIALIDADES_SNOMED = {
    "cardiologia": ("394579002", "Cardiology"),
    "medicina interna": ("419192003", "Internal medicine"),
    "cirugia cardiovascular": ("408466002", "Cardiac surgery"),
    "medicina general": ("394814009", "General practice"),
    "medicina de urgencias": ("773568002", "Emergency medicine"),
}


def sistema(nombre: str) -> str:
    return f"{settings.interop_sistema_base.rstrip('/')}/identifier/{nombre}"


# ---------- errores ----------
class InteropError(Exception):
    def __init__(self, status: int, issues: list[tuple[str, str]] | str, *, code: str = "invalid",
                 cabeceras: dict | None = None, auditar: bool = True):
        self.status = status
        self.issues = [(code, issues)] if isinstance(issues, str) else issues
        self.cabeceras = cabeceras or {}
        self.auditar = auditar
        super().__init__("; ".join(d for _, d in self.issues))


def operation_outcome(issues: list[tuple[str, str]], severity: str = "error") -> dict:
    return {"resourceType": "OperationOutcome",
            "issue": [{"severity": severity, "code": c, "diagnostics": d} for c, d in issues]}


def respuesta_fhir(contenido: dict, status: int = 200, cabeceras: dict | None = None) -> JSONResponse:
    return JSONResponse(contenido, status_code=status, media_type=FHIR_JSON, headers=cabeceras)


# ---------- utilidades ----------
def _codings(cc: Any) -> list[dict]:
    return [c for c in (cc or {}).get("coding") or [] if isinstance(c, dict)] if isinstance(cc, dict) else []


def _texto(cc: Any) -> str:
    if not isinstance(cc, dict):
        return ""
    return (cc.get("text") or next((c.get("display") for c in _codings(cc) if c.get("display")), "") or "").strip()


def ahora_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.replace(microsecond=0).isoformat()


def parsear_fecha_hora(valor: Any) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        if FECHA_RE.match(valor):
            partes = (valor + "-01-01")[:10].split("-")
            return datetime(int(partes[0]), int(partes[1]), int(partes[2]))
        return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def sin_contacto(recurso: dict) -> dict:
    return {k: v for k, v in recurso.items() if k not in CAMPOS_CONTACTO}


def _uuid5(texto: str) -> str:
    return "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, texto))


# ---------- Bundle entrante ----------
@dataclass
class PacienteEntrante:
    ref_externa: str | None
    tipo_doc: str | None
    documento: str | None
    federado: str | None
    nombre: str | None
    apellido: str | None
    genero: str | None
    nacimiento: date | None


@dataclass
class ObservacionEntrante:
    codigo: str
    nombre: str
    tipo: str              # NUMERIC | TEXT | BOOLEAN | COMPONENTS
    valor: float | None = None
    texto: str | None = None
    booleano: bool | None = None
    unidad: str | None = None
    componentes: list[dict] = field(default_factory=list)
    fecha: datetime | None = None


@dataclass
class RemisionEntrante:
    paciente: PacienteEntrante
    clave: str
    sr_id: str | None
    sr_identifier: dict | None
    task_id: str | None
    motivo: str
    prioridad: str
    especialidad: str | None
    solicitante: str | None
    observaciones: list[ObservacionEntrante]
    reporte: dict | None
    consentimiento: dict | None
    bundle_minimo: dict


def _identificadores_paciente(p: dict) -> tuple[str | None, str | None, str | None, list[str]]:
    tipo = documento = federado = None
    problemas: list[str] = []
    for ident in p.get("identifier") or []:
        if not isinstance(ident, dict) or not ident.get("value"):
            continue
        sis = str(ident.get("system") or "")
        valor = str(ident["value"]).strip()
        if "federado" in sis.lower():
            federado = valor.lower()
            continue
        t = next((str(c.get("code")).upper() for c in _codings(ident.get("type")) if c.get("code")), None)
        t = t or (str((ident.get("type") or {}).get("text") or "").strip().upper() or None)
        if not t:
            final = sis.rstrip("/").split(":")[-1].split("/")[-1].upper()
            t = final if final in TIPOS_DOCUMENTO else None
        if t in TIPOS_DOCUMENTO:
            tipo, documento = t, valor
        elif documento is None and t is None and ("document" in sis.lower() or "cedula" in sis.lower()):
            problemas.append("Patient.identifier: el documento debe traer su tipo (type.coding.code CC, TI, CE, PA, RC…)")
    return tipo, documento, federado, problemas


def validar_bundle(bundle: Any) -> RemisionEntrante:
    if not isinstance(bundle, dict):
        raise InteropError(422, "El cuerpo debe ser un objeto JSON")
    issues: list[tuple[str, str]] = []

    def mal(diag: str, code: str = "invalid"):
        issues.append((code, diag))

    if bundle.get("resourceType") != "Bundle":
        raise InteropError(422, "resourceType debe ser Bundle")
    if bundle.get("type") not in TIPOS_BUNDLE:
        mal(f"Bundle.type debe ser uno de {sorted(TIPOS_BUNDLE)}")
    entradas = bundle.get("entry")
    if not isinstance(entradas, list) or not entradas:
        raise InteropError(422, "Bundle.entry está vacío")
    if len(entradas) > 200:
        raise InteropError(422, "Bundle con demasiadas entradas (máximo 200)", code="too-costly")

    por_tipo: dict[str, list[dict]] = {}
    refs: dict[str, dict] = {}
    for i, e in enumerate(entradas):
        r = (e or {}).get("resource") if isinstance(e, dict) else None
        if not isinstance(r, dict) or not r.get("resourceType"):
            mal(f"entry[{i}] sin resource o sin resourceType")
            continue
        rt = r["resourceType"]
        por_tipo.setdefault(rt, []).append(r)
        if e.get("fullUrl"):
            refs[str(e["fullUrl"])] = r
        if r.get("id"):
            refs[f"{rt}/{r['id']}"] = r
        url = str(((e.get("request") or {}).get("url")) or "")
        if re.match(r"^[A-Za-z]+/[^/?]+$", url):
            refs[url] = r

    pacientes = por_tipo.get("Patient", [])
    srs = por_tipo.get("ServiceRequest", [])
    if len(pacientes) != 1:
        mal(f"Se esperaba exactamente 1 Patient y llegaron {len(pacientes)}", "required")
    if len(srs) != 1:
        mal(f"Se esperaba exactamente 1 ServiceRequest y llegaron {len(srs)}", "required")
    if issues:
        raise InteropError(422, issues)
    p, sr = pacientes[0], srs[0]

    def apunta_al_paciente(ref: Any) -> bool:
        if not isinstance(ref, dict):
            return False
        if ref.get("reference"):
            return refs.get(str(ref["reference"])) is p
        ident = ref.get("identifier")
        return isinstance(ident, dict) and any(
            isinstance(x, dict) and x.get("value") == ident.get("value") for x in p.get("identifier") or [])

    # Patient
    tipo, documento, federado, probs = _identificadores_paciente(p)
    for d in probs:
        mal(d)
    if not (documento or federado or p.get("id")):
        mal("Patient: falta un identificador (documento con su tipo, id federado o id)", "required")
    nacimiento = None
    if p.get("birthDate") is not None:
        bd = str(p["birthDate"])
        if not FECHA_RE.match(bd):
            mal("Patient.birthDate debe ser AAAA, AAAA-MM o AAAA-MM-DD")
        else:
            partes = (bd + "-01-01")[:10].split("-")
            try:
                nacimiento = date(int(partes[0]), int(partes[1]), int(partes[2]))
            except ValueError:
                mal("Patient.birthDate no es una fecha válida")
    genero = None
    if p.get("gender") is not None:
        genero = GENEROS.get(str(p["gender"]).lower())
        if genero is None:
            mal("Patient.gender debe ser male, female, other o unknown")
    nombre = apellido = None
    nombres = p.get("name") or []
    if nombres and isinstance(nombres[0], dict):
        n0 = nombres[0]
        nombre = " ".join(str(x) for x in (n0.get("given") or []) if x).strip() or None
        apellido = (str(n0.get("family") or "").strip() or None)
        if not (nombre or apellido) and n0.get("text"):
            nombre = str(n0["text"]).strip()
    paciente = PacienteEntrante(p.get("id"), tipo, documento, federado, nombre, apellido, genero, nacimiento)

    # ServiceRequest
    if sr.get("status") != "active":
        mal("ServiceRequest.status debe ser active")
    if sr.get("intent") not in INTENTS:
        mal(f"ServiceRequest.intent debe ser uno de {sorted(INTENTS)}")
    cod = sr.get("code")
    if not (any(c.get("system") == SNOMED and c.get("code") for c in _codings(cod)) or _texto(cod)):
        mal("ServiceRequest.code debe traer un código SNOMED CT (p. ej. 3457005) o al menos text")
    if not apunta_al_paciente(sr.get("subject")):
        mal("ServiceRequest.subject debe referenciar al Patient del Bundle")
    motivo = " ".join(t for t in (_texto(x) for x in sr.get("reasonCode") or []) if t).strip()
    if not motivo:
        motivo = " ".join(str(n.get("text") or "") for n in sr.get("note") or [] if isinstance(n, dict)).strip()
    if len(motivo) < 5:
        mal("ServiceRequest: el motivo (reasonCode[].text o note) debe tener al menos 5 caracteres", "required")
    prioridad = sr.get("priority") or "routine"
    if prioridad not in PRIORIDADES:
        mal(f"ServiceRequest.priority debe ser uno de {sorted(PRIORIDADES)}")
    sr_identifier = next((x for x in sr.get("identifier") or [] if isinstance(x, dict) and x.get("value")), None)
    if not (sr_identifier or sr.get("id")):
        mal("ServiceRequest: id o identifier es obligatorio (evita recibir dos veces la misma remisión)", "required")
    clave = (f"{sr_identifier.get('system') or ''}|{sr_identifier['value']}" if sr_identifier else f"id|{sr.get('id')}")[:300]
    especialidad = _texto(sr.get("performerType")) or None
    solicitante = (sr.get("requester") or {}).get("display") if isinstance(sr.get("requester"), dict) else None

    # Observation
    observaciones: list[ObservacionEntrante] = []
    for i, o in enumerate(por_tipo.get("Observation", [])):
        donde = f"Observation[{i}]" + (f" ({o.get('id')})" if o.get("id") else "")
        loinc = next((c for c in _codings(o.get("code")) if c.get("system") == LOINC and c.get("code")), None)
        if loinc is None:
            mal(f"{donde}: code debe traer un código LOINC (system {LOINC})")
        if o.get("status") not in OBS_STATUS:
            mal(f"{donde}: status debe ser uno de {sorted(OBS_STATUS)}")
        if not apunta_al_paciente(o.get("subject")):
            mal(f"{donde}: subject debe referenciar al Patient del Bundle")
        fecha = parsear_fecha_hora(o.get("effectiveDateTime") or o.get("issued"))
        if (o.get("effectiveDateTime") or o.get("issued")) and fecha is None:
            mal(f"{donde}: effectiveDateTime no es ISO 8601")
        obs = None
        nombre_obs = (loinc or {}).get("display") or _texto(o.get("code")) or (loinc or {}).get("code") or "Observation"
        vq = o.get("valueQuantity")
        if isinstance(vq, dict):
            if not isinstance(vq.get("value"), (int, float)) or isinstance(vq.get("value"), bool):
                mal(f"{donde}: valueQuantity.value debe ser numérico")
            elif vq.get("system") and vq.get("system") != UCUM:
                mal(f"{donde}: valueQuantity.system debe ser UCUM ({UCUM})")
            else:
                obs = ObservacionEntrante(loinc["code"] if loinc else "", nombre_obs, "NUMERIC",
                                          valor=float(vq["value"]), unidad=vq.get("code") or vq.get("unit"))
        elif isinstance(o.get("valueString"), str):
            obs = ObservacionEntrante((loinc or {}).get("code", ""), nombre_obs, "TEXT", texto=o["valueString"][:2000])
        elif isinstance(o.get("valueCodeableConcept"), dict):
            obs = ObservacionEntrante((loinc or {}).get("code", ""), nombre_obs, "TEXT",
                                      texto=_texto(o["valueCodeableConcept"])[:2000] or "(código)")
        elif isinstance(o.get("valueBoolean"), bool):
            obs = ObservacionEntrante((loinc or {}).get("code", ""), nombre_obs, "BOOLEAN", booleano=o["valueBoolean"])
        elif isinstance(o.get("component"), list) and o["component"]:
            comps = []
            for j, c in enumerate(o["component"]):
                cl = next((x for x in _codings((c or {}).get("code")) if x.get("code")), None)
                cv = (c or {}).get("valueQuantity") or {}
                if cl is None or not isinstance(cv.get("value"), (int, float)):
                    mal(f"{donde}.component[{j}]: necesita code y valueQuantity.value numérico")
                    continue
                comps.append({"system": cl.get("system"), "code": cl["code"], "display": cl.get("display"),
                              "valor": float(cv["value"]), "unidad": cv.get("code") or cv.get("unit")})
            obs = ObservacionEntrante((loinc or {}).get("code", ""), nombre_obs, "COMPONENTS", componentes=comps)
        else:
            mal(f"{donde}: falta el valor (valueQuantity, valueString, valueCodeableConcept, valueBoolean o component)")
        if obs is not None and loinc is not None:
            obs.fecha = fecha
            observaciones.append(obs)

    # DiagnosticReport (solo el de IA aprobado)
    drs = por_tipo.get("DiagnosticReport", [])
    reporte = None
    if len(drs) > 1:
        mal("Se admite como máximo 1 DiagnosticReport (el reporte de IA aprobado)")
    elif drs:
        dr = drs[0]
        if dr.get("status") not in DR_APROBADO:
            mal("DiagnosticReport.status debe ser final, amended o corrected: solo se acepta el reporte de IA APROBADO", "business-rule")
        if not apunta_al_paciente(dr.get("subject")):
            mal("DiagnosticReport.subject debe referenciar al Patient del Bundle")
        reporte = {"id": dr.get("id"), "status": dr.get("status"), "codigo": _texto(dr.get("code")),
                   "conclusion": str(dr.get("conclusion") or "")[:4000], "emitido": dr.get("issued")}

    # Consent (evidencia) y Task (para responderles)
    consentimiento = None
    cons = por_tipo.get("Consent", [])
    if len(cons) > 1:
        mal("Se admite como máximo 1 Consent")
    elif cons:
        if cons[0].get("status") != "active":
            mal("Consent.status debe ser active")
        consentimiento = cons[0]
    task_id = None
    tasks = por_tipo.get("Task", [])
    if len(tasks) > 1:
        mal("Se admite como máximo 1 Task")
    elif tasks:
        if tasks[0].get("status") not in {"requested", "draft", "received", None}:
            mal("Task.status debe ser requested")
        task_id = tasks[0].get("id")

    if issues:
        raise InteropError(422, issues)

    minimo = dict(bundle)
    minimo["entry"] = [dict(e, resource=sin_contacto(e["resource"])) if isinstance(e, dict) and isinstance(e.get("resource"), dict)
                       and e["resource"].get("resourceType") == "Patient" else e for e in entradas]
    return RemisionEntrante(paciente, clave, sr.get("id"), sr_identifier, task_id, motivo[:2000], prioridad,
                            especialidad, solicitante, observaciones, reporte, consentimiento, minimo)


# ---------- Task ----------
def numero_desde(texto: Any) -> int | None:
    """'rem-12', 'ServiceRequest/rem-12', '12' -> 12."""
    if texto is None:
        return None
    m = re.match(r"^(?:[A-Za-z]+/)?(?:rem-)?(\d{1,18})$", str(texto).strip())
    return int(m.group(1)) if m else None


def referencias_task(task: dict) -> tuple[list[int], list[str]]:
    """Números nuestros candidatos y ids crudos que el socio usó para señalar la remisión."""
    numeros: list[int] = []
    crudos: list[str] = []
    refs = []
    if isinstance(task.get("focus"), dict):
        refs.append(task["focus"])
    refs += [x for x in task.get("basedOn") or [] if isinstance(x, dict)]
    for r in refs:
        for valor in (r.get("reference"), (r.get("identifier") or {}).get("value") if isinstance(r.get("identifier"), dict) else None):
            if valor:
                crudos.append(str(valor).split("/")[-1])
                n = numero_desde(valor)
                if n is not None:
                    numeros.append(n)
    for ident in task.get("identifier") or []:
        if isinstance(ident, dict) and ident.get("system") == sistema("remision") and numero_desde(ident.get("value")) is not None:
            numeros.append(numero_desde(ident["value"]))
    if task.get("id"):
        crudos.append(str(task["id"]))
        n = numero_desde(task["id"])
        if n is not None:
            numeros.append(n)
    return list(dict.fromkeys(numeros)), list(dict.fromkeys(crudos))


def motivo_task(task: dict) -> str | None:
    for valor in (_texto(task.get("statusReason")), _texto(task.get("businessStatus")),
                  " ".join(str(n.get("text") or "") for n in task.get("note") or [] if isinstance(n, dict)).strip()):
        if valor:
            return valor[:2000]
    return None


ESTADO_NEGOCIO = {
    "requested": "Recibida: pendiente de revisión por el especialista",
    "accepted": "Aceptada: el especialista atenderá al paciente",
    "rejected": "Rechazada por el especialista",
    "completed": "Completada: el paciente ya fue atendido",
    "in-progress": "En curso",
    "cancelled": "Cancelada",
    "failed": "Fallida",
}


def task_de_entrante(rx, socio_nombre: str | None = None) -> dict:
    """El Task que describe una remisión que NOS enviaron (respuesta 201, GET y aviso de aceptación/rechazo)."""
    t: dict[str, Any] = {
        "resourceType": "Task",
        "id": rx.task_id_externo or f"rem-{rx.numero}",
        "identifier": [{"system": sistema("remision"), "value": str(rx.numero)}],
        "status": rx.estado,
        "intent": "order",
        "priority": rx.prioridad,
        "code": {"coding": [{"system": SNOMED, "code": "3457005", "display": "Patient referral"}]},
        "businessStatus": {"text": ESTADO_NEGOCIO.get(rx.estado, rx.estado)},
        "owner": {"display": settings.interop_nombre},
        "authoredOn": iso(rx.creado),
        "lastModified": iso(rx.actualizado) or ahora_iso(),
    }
    sr_ref: dict[str, Any] = {"type": "ServiceRequest"}
    if rx.sr_id_externo:
        sr_ref["reference"] = f"ServiceRequest/{rx.sr_id_externo}"
    ident = rx.sr_identifier or ({"value": rx.sr_id_externo} if rx.sr_id_externo else None)
    if ident:
        sr_ref["identifier"] = {k: v for k, v in ident.items() if k in ("system", "value")}
    if len(sr_ref) > 1:
        t["focus"] = sr_ref
        t["basedOn"] = [sr_ref]
    if rx.patient_ref_externo:
        t["for"] = {"reference": f"Patient/{rx.patient_ref_externo}"}
    if rx.respuesta_motivo:
        t["statusReason"] = {"text": rx.respuesta_motivo}
    if socio_nombre:
        t["requester"] = {"display": socio_nombre}
    return t


def task_de_saliente(rx) -> dict:
    """Nuestro Task para una remisión que NOSOTROS enviamos (respuesta del POST /interop/Task)."""
    t = {
        "resourceType": "Task", "id": f"rem-{rx.numero}",
        "identifier": [{"system": sistema("remision"), "value": str(rx.numero)}],
        "status": rx.estado, "intent": "order", "priority": rx.prioridad,
        "code": {"coding": [{"system": SNOMED, "code": "3457005", "display": "Patient referral"}]},
        "focus": {"reference": f"ServiceRequest/rem-{rx.numero}", "type": "ServiceRequest"},
        "basedOn": [{"reference": f"ServiceRequest/rem-{rx.numero}", "type": "ServiceRequest"}],
        "businessStatus": {"text": ESTADO_NEGOCIO.get(rx.estado, rx.estado)},
        "requester": {"display": settings.interop_nombre},
        "lastModified": iso(rx.actualizado) or ahora_iso(),
    }
    if rx.respuesta_motivo:
        t["statusReason"] = {"text": rx.respuesta_motivo}
    return t


# ---------- Bundle saliente ----------
UNIDADES_UCUM = {"bpm": "/min", "lpm": "/min", "latidos/min": "/min", "rpm": "/min", "mmhg": "mm[Hg]",
                 "°c": "Cel", "c": "Cel", "ºc": "Cel", "meq/l": "meq/L", "%": "%", "kg": "kg", "cm": "cm",
                 "mg/dl": "mg/dL", "mmol/l": "mmol/L", "u/l": "U/L", "kg/m2": "kg/m2"}

# Variables del modelo de riesgo que tienen LOINC (las demás son antecedentes sí/no)
VARIABLES_IA = {
    "serum_creatinine": ("2160-0", "Creatinine [Mass/volume] in Serum or Plasma", "mg/dL", 1.0, "laboratory"),
    "serum_sodium": ("2951-2", "Sodium [Moles/volume] in Serum or Plasma", "mmol/L", 1.0, "laboratory"),
    "ejection_fraction": ("10230-1", "Left ventricular Ejection fraction", "%", 1.0, "imaging"),
    "creatinine_phosphokinase": ("2157-6", "Creatine kinase [Enzymatic activity/volume] in Serum or Plasma", "U/L", 1.0, "laboratory"),
    "platelets": ("777-3", "Platelets [#/volume] in Blood by Automated count", "10*3/uL", 0.001, "laboratory"),
}


def ucum(unidad: str | None) -> str | None:
    if not unidad:
        return None
    return UNIDADES_UCUM.get(unidad.strip().lower(), unidad.strip())


def _categoria(codigo: str) -> str:
    return "vital-signs" if codigo in {"29463-7", "8867-4", "85354-9", "8480-6", "8462-4", "8310-5", "9279-1", "59408-5", "39156-5", "8302-2"} else "laboratory"


def observacion_saliente(obs_id: str, pac_ref: str, codigo: str, nombre: str, fecha: datetime | None, *,
                         valor: float | None = None, unidad: str | None = None, texto: str | None = None,
                         componentes: list[dict] | None = None, categoria: str | None = None) -> dict:
    o: dict[str, Any] = {
        "resourceType": "Observation", "id": obs_id, "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                  "code": categoria or _categoria(codigo)}]}],
        "code": {"coding": [{"system": LOINC, "code": codigo, "display": nombre}]},
        "subject": {"reference": pac_ref},
        "effectiveDateTime": iso(fecha) or ahora_iso(),
    }
    if componentes:
        o["component"] = [{"code": {"coding": [{"system": LOINC, "code": c["code"], "display": c.get("display")}]},
                           "valueQuantity": {"value": c["valor"], "unit": c.get("unidad"), "system": UCUM, "code": ucum(c.get("unidad"))}}
                          for c in componentes]
    elif valor is not None:
        u = ucum(unidad) or "1"
        o["valueQuantity"] = {"value": valor, "unit": unidad or u, "system": UCUM, "code": u}
    else:
        o["valueString"] = texto or ""
    return o


def entrada(recurso: dict) -> dict:
    rt, rid = recurso["resourceType"], recurso["id"]
    return {"fullUrl": _uuid5(f"{settings.interop_sistema_base}/{rt}/{rid}"), "resource": recurso,
            "request": {"method": "PUT", "url": f"{rt}/{rid}"}}


def especialidad_fhir(texto: str | None) -> dict:
    t = (texto or "Medicina interna").strip()
    clave = t.lower().translate(str.maketrans("áéíóú", "aeiou"))
    sn = ESPECIALIDADES_SNOMED.get(clave)
    return {"coding": [{"system": SNOMED, "code": sn[0], "display": sn[1]}] if sn else [], "text": t}
