import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";

// Semana 12 (RAG) y Semana 13 (agente con aprobación humana) desde el frontend.
// Solo médico y especialista: el backend también lo exige (403 para los demás roles).

const EJEMPLOS_RAG = [
  "¿Desde qué valor de sodio se habla de hiponatremia?",
  "¿Cuándo remitir a cardiología a un paciente con FEVI baja?",
  "¿Qué riesgo y qué criterios críticos tiene en su último reporte?",
];
const EJEMPLOS_AGENTE = [
  "¿Qué riesgo tiene este paciente según su reporte de IA y qué significa?",
  "Resume los datos y las últimas observaciones de este paciente.",
  "Remite este paciente a cardiología si cumple criterios.",
  "Recuerda que prefiero respuestas en viñetas y muy breves.",
];

function Texto({ value }) {
  // Markdown mínimo: viñetas, **negritas** y citas [n] resaltadas.
  const lineas = (value || "").split("\n");
  const inline = (t, k) =>
    t.split(/(\*\*[^*]+\*\*|\[\d+\])/g).map((p, i) =>
      p.startsWith("**") ? <strong key={`${k}-${i}`}>{p.slice(2, -2)}</strong>
        : /^\[\d+\]$/.test(p) ? <span key={`${k}-${i}`} className="cita">{p}</span>
          : p);
  return (
    <div className="as-texto">
      {lineas.map((l, i) => {
        const vineta = l.match(/^\s*[-*•]\s+(.*)/);
        if (vineta) return <div key={i} className="as-vineta">• {inline(vineta[1], i)}</div>;
        if (!l.trim()) return <div key={i} className="as-espacio" />;
        return <p key={i}>{inline(l.replace(/^#+\s*/, ""), i)}</p>;
      })}
    </div>
  );
}

function Modo({ modo }) {
  const tipo = modo === "llm" ? "ok" : modo === "abstencion" ? "warn" : "info";
  const texto = modo === "llm" ? "Redactado por LLM" : modo === "abstencion" ? "Abstención" : `Extractivo${modo?.startsWith("extractivo (") ? " · LLM no disponible" : ""}`;
  return <span className={`as-badge ${tipo}`}>{texto}</span>;
}

function PanelRag({ patient }) {
  const [pregunta, setPregunta] = useState(EJEMPLOS_RAG[0]);
  const [conPaciente, setConPaciente] = useState(true);
  const [metodo, setMetodo] = useState("hibrido");
  const [resultado, setResultado] = useState(null);
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);

  async function preguntar(e) {
    e.preventDefault();
    setCargando(true); setError(""); setResultado(null);
    try {
      const body = { pregunta, metodo };
      if (conPaciente && patient) body.paciente_id = patient.id;
      setResultado(await api("/rag/preguntar", { method: "POST", body: JSON.stringify(body) }));
    } catch (err) { setError(err.message); }
    setCargando(false);
  }

  return (
    <div className="as-tab">
      <p className="muted">Busca en las guías de insuficiencia cardiaca y en la historia <b>aprobada</b> del paciente abierto. Responde con citas [n] o se abstiene si no hay evidencia.</p>
      <div className="as-ejemplos">{EJEMPLOS_RAG.map((q) => <button key={q} type="button" onClick={() => setPregunta(q)}>{q}</button>)}</div>
      <form onSubmit={preguntar} className="as-form">
        <textarea value={pregunta} onChange={(e) => setPregunta(e.target.value)} rows={2} maxLength={500} placeholder="Escribe tu pregunta clínica" />
        <div className="as-opciones">
          <label className="as-check"><input type="checkbox" checked={conPaciente} disabled={!patient} onChange={(e) => setConPaciente(e.target.checked)} />
            Incluir historia del paciente abierto {patient ? <b>({patient.patient_code})</b> : <i>(ninguno seleccionado)</i>}</label>
          <select value={metodo} onChange={(e) => setMetodo(e.target.value)} className="as-select">
            <option value="hibrido">Híbrido (RRF)</option><option value="denso">Denso (pgvector)</option><option value="bm25">BM25</option>
          </select>
          <button className="primary" disabled={cargando || pregunta.trim().length < 3}>{cargando ? "Buscando..." : "Preguntar"}</button>
        </div>
      </form>
      {error && <div className="c2-notice error">{error}</div>}
      {resultado && (
        <div className="as-resultado">
          <div className="row-between">
            <div className="as-meta"><Modo modo={resultado.modo} /><span className="pill">{resultado.metodo}</span>{resultado.seudonimo && <span className="pill">{resultado.seudonimo}</span>}</div>
            <small className="muted">coseno {resultado.puntajes?.max_coseno} · BM25 {resultado.puntajes?.max_bm25}</small>
          </div>
          <Texto value={resultado.respuesta} />
          {resultado.fuentes?.length > 0 && <div className="as-fuentes">
            <strong>Fuentes</strong>
            {resultado.fuentes.map((f) => (
              <div key={f.chunk_id} className="as-fuente">
                <span className="cita">[{f.n}]</span>
                <div><div className="as-fuente-tit"><b>{f.doc_id}</b><span className={`as-estado ${f.estado}`}>{f.tipo} · {f.estado}</span></div>
                  <small title={f.fuente}>{f.fuente.length > 150 ? f.fuente.slice(0, 150) + "..." : f.fuente}</small></div>
              </div>))}
          </div>}
        </div>
      )}
    </div>
  );
}

function PanelAgente({ me, patient }) {
  const esMedico = ["MEDICO", "MEDICO_RURAL"].includes(me?.role?.code);
  const convPorDefecto = patient ? `paciente-${patient.id.slice(0, 8)}` : "general";
  const [conversacion, setConversacion] = useState(convPorDefecto);
  const [mensajes, setMensajes] = useState([]);
  const [pendiente, setPendiente] = useState(null);
  const [texto, setTexto] = useState("");
  const [motivo, setMotivo] = useState("");
  const [estadoAgente, setEstadoAgente] = useState(null);
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);
  const chatRef = useRef(null);

  useEffect(() => {   // baja al último mensaje
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [mensajes, cargando]);
  useEffect(() => { api("/agente/estado").then(setEstadoAgente).catch(() => {}); }, []);
  useEffect(() => { setConversacion(convPorDefecto); }, [convPorDefecto]);
  useEffect(() => {   // al abrir una conversación se recupera su historia (persistida en PostgreSQL)
    setPendiente(null); setError("");
    api(`/agente/conversaciones/${conversacion}`)
      .then((r) => {
        setMensajes(r.mensajes.filter((m) => m.rol === "usuario" || m.rol === "agente")
          .map((m) => ({ de: m.rol === "usuario" ? "yo" : "agente", texto: m.texto })));
        if (r.pendiente) setPendiente({ acciones: r.pendiente });   // sigue pendiente aunque se haya recargado
      })
      .catch(() => setMensajes([]));
  }, [conversacion]);

  function aplicar(r) {
    if (r.estado === "pendiente_aprobacion") {
      if (r.acciones) setPendiente(r);
      else setError(r.detalle || "Hay una acción esperando su decisión.");
      if (r.acciones) setMensajes((m) => [...m, { de: "agente", texto: "Propongo la siguiente acción. Necesito su aprobación para ejecutarla.", herramientas: r.herramientas }]);
    } else if (r.estado === "respondido") {
      setPendiente(null);
      setMensajes((m) => [...m, { de: "agente", texto: r.respuesta, herramientas: r.herramientas, duracion: r.duracion_s, fuentes: r.fuentes }]);
    } else if (r.estado === "rechazado_seguridad") {
      setMensajes((m) => [...m, { de: "sistema", texto: r.respuesta + (r.bloqueado_hasta ? " Acceso al asistente bloqueado temporalmente." : "") }]);
    } else if (r.estado === "tiempo_agotado" || r.estado === "error_proveedor") {
      setMensajes((m) => [...m, { de: "sistema", texto: r.detalle }]);
    } else if (r.estado === "sin_pendientes") {
      setPendiente(null); setError("No hay acciones pendientes en esta conversación.");
    }
  }

  async function enviar(e) {
    e?.preventDefault();
    if (texto.trim().length < 2) return;
    const mensaje = texto.trim();
    setMensajes((m) => [...m, { de: "yo", texto: mensaje }]); setTexto(""); setCargando(true); setError("");
    try {
      const body = { mensaje, conversacion_id: conversacion };
      if (patient) body.paciente_id = patient.id;
      aplicar(await api("/agente/chat", { method: "POST", body: JSON.stringify(body) }));
    } catch (err) { setError(err.message); }
    setCargando(false);
  }

  async function decidir(decision) {
    setCargando(true); setError("");
    try {
      const body = { conversacion_id: conversacion, decision };
      if (decision === "reject") body.motivo = motivo || "El médico rechazó la remisión.";
      setMensajes((m) => [...m, { de: "yo", texto: decision === "approve" ? "✔ Aprobado" : `✖ Rechazado: ${body.motivo}` }]);
      aplicar(await api("/agente/decidir", { method: "POST", body: JSON.stringify(body) }));
      setMotivo("");
    } catch (err) { setError(err.message); }
    setCargando(false);
  }

  return (
    <div className="as-tab">
      <div className="row-between as-cabecera">
        <p className="muted">El agente consulta al paciente abierto, sus reportes de IA y las guías. <b>Propone</b>; las remisiones solo se crean si el médico aprueba.</p>
        {estadoAgente && <div className="as-meta"><span className="pill">{estadoAgente.modelo}</span><span className="pill">memoria: {estadoAgente.memoria}</span></div>}
      </div>
      <div className="as-conv">
        <span>Paciente abierto: {patient ? <b>{patient.patient_code}</b> : <i>ninguno (solo guías)</i>}</span>
        <span>Conversación: <code>{conversacion}</code></span>
        <button type="button" onClick={() => setConversacion(`${convPorDefecto}-${Date.now().toString(36).slice(-4)}`)}>Nueva conversación</button>
      </div>

      <div className="as-chat" ref={chatRef}>
        {mensajes.length === 0 && <p className="muted as-vacio">Sin mensajes. Prueba con un ejemplo:</p>}
        {mensajes.map((m, i) => (
          <div key={i} className={`as-msg ${m.de}`}>
            {m.de === "yo" ? <p>{m.texto}</p> : <Texto value={m.texto} />}
            {m.herramientas?.length > 0 && <div className="as-herr">{m.herramientas.map((h, j) => <span key={j} className="pill">{h}</span>)}{m.duracion && <small>{m.duracion} s</small>}</div>}
            {m.fuentes?.length > 0 && <details className="as-fuentes"><summary>Fuentes ({m.fuentes.length})</summary>
              {m.fuentes.map((f, j) => <div key={j} className="as-fuente">
                <span className="cita">{f.n ? `[${f.n}]` : "·"}</span>
                <div><div className="as-fuente-tit"><b>{f.documento}</b><span className={`as-estado ${f.estado || ""}`}>{f.tipo}{f.estado ? ` · ${f.estado}` : ""}</span></div>
                  <small>{f.fragmento}</small></div>
              </div>)}
            </details>}
          </div>))}
        {cargando && <div className="as-msg agente pensando">El agente está pensando… con Groq puede tardar unos segundos.</div>}
      </div>

      {pendiente && (
        <div className="as-aprobacion">
          <strong>Acción pendiente de aprobación</strong>
          {pendiente.acciones.map((a, i) => (
            <div key={i} className="as-accion"><span className="pill as-pill-inicio">{a.herramienta}</span>
              {Object.entries(a.args).map(([k, v]) => <div key={k}><b>{k}:</b> {String(v)}</div>)}</div>))}
          {esMedico ? <div className="as-botones">
            <button className="primary" onClick={() => decidir("approve")} disabled={cargando}>Aprobar</button>
            <input value={motivo} onChange={(e) => setMotivo(e.target.value)} placeholder="Motivo del rechazo (opcional)" maxLength={300} />
            <button className="as-rechazar" onClick={() => decidir("reject")} disabled={cargando}>Rechazar</button>
          </div> : <div className="c2-notice">Solo el médico aprueba o rechaza remisiones (R08).</div>}
        </div>)}

      {error && <div className="c2-notice error">{error}</div>}
      <div className="as-ejemplos">{EJEMPLOS_AGENTE.map((q) => <button key={q} type="button" onClick={() => setTexto(q)}>{q}</button>)}</div>
      <form onSubmit={enviar} className="as-enviar">
        <textarea value={texto} onChange={(e) => setTexto(e.target.value)} rows={2} maxLength={1000} disabled={cargando || !!pendiente}
          placeholder={pendiente ? "Primero apruebe o rechace la acción pendiente" : "Escribe al agente"}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) enviar(e); }} />
        <button className="primary" disabled={cargando || !!pendiente || texto.trim().length < 2}>Enviar</button>
      </form>
    </div>
  );
}

export default function AsistentePanel({ me, patient }) {
  const [tab, setTab] = useState("agente");
  const titulo = useMemo(() => (tab === "agente" ? "Agente clínico" : "Preguntar al RAG"), [tab]);
  return (
    <section className="card asistente">
      <div className="row-between">
        <h2>Asistente clínico · {titulo}</h2>
        <div className="as-tabs">
          <button className={tab === "agente" ? "activo" : ""} onClick={() => setTab("agente")}>Agente (semana 13)</button>
          <button className={tab === "rag" ? "activo" : ""} onClick={() => setTab("rag")}>RAG (semana 12)</button>
        </div>
      </div>
      {tab === "agente" ? <PanelAgente me={me} patient={patient} /> : <PanelRag patient={patient} />}
    </section>
  );
}
