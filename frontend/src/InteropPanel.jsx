import { useEffect, useState } from "react";
import { API_URL, api, getToken } from "./api";

// Interoperabilidad entre instituciones (proyecto final, R30–R33).
// Médico: consentimiento por institución, remitir y seguir el estado. Especialista: recibir, aceptar o
// rechazar con motivo. Admin: instituciones socias (URL, credenciales, espejo). Todo se refresca en vivo.

const ESPECIALIDADES = ["Medicina interna", "Cardiología", "Cirugía cardiovascular", "Medicina general", "Medicina de urgencias"];
const PRIORIDADES = [["routine", "Rutina"], ["urgent", "Urgente"], ["asap", "Lo antes posible"], ["stat", "Inmediata"]];
const ESTADOS = { requested: "pendiente", accepted: "aceptada", rejected: "rechazada", completed: "completada", "in-progress": "en curso", cancelled: "cancelada", failed: "fallida" };

// Las instituciones que crean scripts/test_interop.py (prueba-xxxx, firmado-xxxx) no se muestran en las listas.
const esPrueba = (rx) => /^(prueba|firmado)-[0-9a-f]{6}$/.test(rx.institucion || "");

const roleOf = (me) => {
  const code = (me?.role?.code || "").toUpperCase();
  if (["MEDICO", "MEDICO_RURAL"].includes(code)) return "medico";
  if (code === "ESPECIALISTA") return "especialista";
  if (code === "ADMIN") return "admin";
  return code.toLowerCase();
};

function Card({ title, children }) {
  return <section className="card corte2-card"><h2>{title}</h2>{children}</section>;
}

function Envio({ estado, http }) {
  if (!estado) return null;
  const txt = estado === "enviada" ? `entregada${http ? ` (${http})` : ""}` : estado === "fallida" ? `falló${http ? ` (${http})` : " (sin conexión)"}` : estado;
  return <span className="pill">{txt}</span>;
}

export default function InteropPanel({ me, selectedPatient, onMessage }) {
  const role = roleOf(me);
  const [instituciones, setInstituciones] = useState([]);
  const [salientes, setSalientes] = useState([]);
  const [entrantes, setEntrantes] = useState([]);
  const [consentimientos, setConsentimientos] = useState([]);
  const [socios, setSocios] = useState([]);
  const [todas, setTodas] = useState([]);
  const [detalle, setDetalle] = useState(null);
  const [credenciales, setCredenciales] = useState(null);
  const [form, setForm] = useState({ institucion: "", motivo: "Insuficiencia cardiaca con riesgo alto de descompensación; valoración por especialista.", prioridad: "urgent", especialidad: "Medicina interna" });
  const [nuevo, setNuevo] = useState({ codigo: "", nombre: "", url_base: "", con_firma: false });
  const [salida, setSalida] = useState({ codigo: "", auth: "oauth2", credencial_id: "", credencial_secreto: "", scope: "", firma_secreto: "" });
  const [urls, setUrls] = useState({});
  const [verInactivas, setVerInactivas] = useState(false);
  const [busy, setBusy] = useState(false);

  const ok = (m) => onMessage && onMessage(m, "");
  const fail = (e) => onMessage && onMessage("", e?.message || String(e));
  async function run(fn) {
    try { setBusy(true); return await fn(); } catch (e) { fail(e); } finally { setBusy(false); }
  }

  async function cargar() {
    if (!["medico", "especialista", "admin"].includes(role)) return;
    const inst = await api("/interop/instituciones").catch(() => []);
    setInstituciones(inst);
    if (!form.institucion && inst.length) setForm((f) => ({ ...f, institucion: (inst.find((i) => i.lista_para_enviar) || inst[0]).codigo }));
    if (role === "medico") setSalientes(await api("/remisiones/externas?direccion=saliente").catch(() => []));
    if (role === "especialista") setEntrantes(await api("/remisiones/externas?direccion=entrante").catch(() => []));
    if (role === "admin") {
      const [s, t] = await Promise.all([api("/interop/socios").catch(() => []), api("/remisiones/externas").catch(() => [])]);
      setSocios(s); setTodas(t);
      setUrls(Object.fromEntries(s.map((x) => [x.codigo, x.url_base || ""])));
    }
    if (selectedPatient && ["medico", "especialista", "admin"].includes(role)) {
      setConsentimientos(await api(`/patients/${selectedPatient.id}/consentimientos`).catch(() => []));
    } else setConsentimientos([]);
  }

  useEffect(() => { cargar().catch(fail); }, [me?.id, selectedPatient?.id]);

  // En vivo: cualquier aviso de remisión externa o de rechazo recarga el panel, sin recargar la página.
  useEffect(() => {
    if (!me || !getToken()) return;
    const base = API_URL.startsWith("http") ? API_URL.replace(/^http/, "ws")
      : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${API_URL}`;
    let socket = null, retry = null, cerrado = false;
    const conectar = () => {
      socket = new WebSocket(`${base}/events/ws?token=${encodeURIComponent(getToken())}`);
      socket.onmessage = (ev) => {
        try {
          const d = JSON.parse(ev.data);
          if (/remision|interop/.test(d.tipo || "")) cargar().catch(() => {});
        } catch {}
      };
      socket.onclose = () => { if (!cerrado) retry = setTimeout(conectar, 3000); };
      socket.onerror = () => { try { socket.close(); } catch {} };
    };
    conectar();
    const respaldo = setInterval(() => cargar().catch(() => {}), 20000);
    return () => { cerrado = true; clearTimeout(retry); clearInterval(respaldo); try { socket && socket.close(); } catch {} };
  }, [me?.id, selectedPatient?.id]);

  // ---------- médico ----------
  const consentimientoDe = (codigo) => consentimientos.find((c) => c.institucion === codigo && c.estado === "activo");

  async function otorgar(codigo) {
    await run(async () => {
      await api(`/patients/${selectedPatient.id}/consentimientos`, { method: "POST", body: JSON.stringify({ institucion: codigo, nota: "Autorización del paciente registrada por el médico" }) });
      await cargar(); ok("Consentimiento registrado para esa institución (Ley 1581 de 2012).");
    });
  }
  async function revocar(c) {
    await run(async () => {
      await api(`/patients/${selectedPatient.id}/consentimientos/${c.id}`, { method: "DELETE" });
      await cargar(); ok("Consentimiento revocado: ya no se puede remitir a esa institución.");
    });
  }
  async function remitir(e) {
    e.preventDefault();
    if (!selectedPatient) return fail(new Error("Selecciona un paciente."));
    await run(async () => {
      const r = await api("/remisiones/externas", { method: "POST", body: JSON.stringify({ ...form, paciente_id: selectedPatient.id }) });
      await cargar();
      if (r.estado_envio === "enviada") ok(`Remisión ${r.codigo} entregada a ${r.institucion_nombre}. La respuesta llegará en vivo.`);
      else fail(new Error(`La remisión ${r.codigo} quedó guardada pero el socio no la recibió (${r.envio_http || "sin conexión"}). Puede reenviarla.`));
    });
  }
  async function reenviar(rx) {
    await run(async () => {
      const r = await api(`/remisiones/externas/${rx.id}/reenviar`, { method: "POST" });
      await cargar();
      const estado = r.direccion === "saliente" ? r.estado_envio : r.respuesta_envio;
      estado === "enviada" ? ok(`Reenvío de ${r.codigo}: entregado.`) : fail(new Error(`Reenvío de ${r.codigo}: el socio sigue sin responder.`));
    });
  }
  async function verBundle(rx) {
    await run(async () => setDetalle(await api(`/remisiones/externas/${rx.id}`)));
  }

  // ---------- especialista ----------
  async function aceptar(rx) {
    const nota = window.prompt("Nota para la institución que remite (opcional):", "");
    if (nota === null) return;
    await run(async () => {
      const r = await api(`/referrals/${rx.remision_id}/accept`, { method: "POST", body: JSON.stringify({ nota }) });
      await cargar();
      r.respuesta_socio?.envio === "enviada" ? ok("Aceptada: la institución fue avisada.") : fail(new Error("Aceptada, pero la institución no recibió la respuesta. Use Reenviar."));
    });
  }
  async function rechazar(rx) {
    const motivo = window.prompt("Motivo del rechazo (obligatorio):", "");
    if (!motivo) return;
    await run(async () => {
      const r = await api(`/referrals/${rx.remision_id}/reject`, { method: "POST", body: JSON.stringify({ motivo }) });
      await cargar();
      r.respuesta_socio?.envio === "enviada" ? ok("Rechazada: la institución recibió el motivo.") : fail(new Error("Rechazada, pero la institución no recibió la respuesta. Use Reenviar."));
    });
  }

  // ---------- admin ----------
  async function crearSocio(e) {
    e.preventDefault();
    await run(async () => {
      const r = await api("/interop/socios", { method: "POST", body: JSON.stringify(nuevo) });
      setCredenciales({ codigo: r.codigo, ...r.credenciales_una_sola_vez });
      setNuevo({ codigo: "", nombre: "", url_base: "", con_firma: false });
      setSalida((s) => ({ ...s, codigo: r.codigo }));
      await cargar(); ok(`Institución ${r.codigo} creada. Copie sus credenciales ahora: no se vuelven a mostrar.`);
    });
  }
  async function guardarSalida(e) {
    e.preventDefault();
    await run(async () => {
      const { codigo, ...cuerpo } = salida;
      await api(`/interop/socios/${codigo}/salida`, { method: "POST", body: JSON.stringify(cuerpo) });
      setSalida({ codigo, auth: "oauth2", credencial_id: "", credencial_secreto: "", scope: "", firma_secreto: "" });
      await cargar(); ok(`Credencial de salida de ${codigo} guardada (cifrada).`);
    });
  }
  async function guardarUrl(s) {
    await run(async () => {
      await api(`/interop/socios/${s.codigo}`, { method: "PUT", body: JSON.stringify({ url_base: urls[s.codigo] }) });
      await cargar(); ok(`URL de ${s.codigo} actualizada, sin redesplegar.`);
    });
  }
  async function cambiarEstado(s) {
    await run(async () => {
      await api(`/interop/socios/${s.codigo}`, { method: "PUT", body: JSON.stringify({ estado: s.estado === "activo" ? "inactivo" : "activo" }) });
      await cargar(); ok(`${s.codigo}: ${s.estado === "activo" ? "desactivada (sus tokens dejan de servir)" : "activada"}.`);
    });
  }
  async function rotar(s) {
    if (!window.confirm(`¿Generar un nuevo secreto para ${s.codigo}? El anterior deja de servir.`)) return;
    await run(async () => {
      const r = await api(`/interop/socios/${s.codigo}/rotar`, { method: "POST" });
      setCredenciales({ codigo: s.codigo, ...r.credenciales_una_sola_vez });
      ok(`Nuevo secreto para ${s.codigo}. Entréguelo por un canal privado.`);
    });
  }
  async function espejo() {
    await run(async () => { await api("/interop/espejo", { method: "POST" }); await cargar(); ok("Espejo listo: remita a 'espejo' para probar el viaje completo contra nosotros mismos."); });
  }

  if (!["medico", "especialista", "admin"].includes(role)) return null;

  const filaRemision = (rx, acciones) => <div className="c2-row" key={rx.id}>
    <div>
      <strong>{rx.codigo} · {rx.direccion === "saliente" ? "→" : "←"} {rx.institucion_nombre} · {ESTADOS[rx.estado] || rx.estado}</strong>
      <span>{rx.motivo}</span>
      {rx.respuesta_motivo && <span><b>Respuesta:</b> {rx.respuesta_motivo}</span>}
      {rx.reporte_ia?.conclusion && <small>Reporte de IA del socio: {rx.reporte_ia.conclusion}</small>}
      <small>{rx.paciente_codigo} · prioridad {rx.prioridad}{rx.especialidad ? ` · ${rx.especialidad}` : ""} · {rx.observaciones} observaciones · {rx.creado ? new Date(rx.creado).toLocaleString("es-CO") : ""}</small>
      {rx.envio_detalle && <small>Detalle del error: {rx.envio_detalle}</small>}
      {rx.respuesta_detalle && <small>Detalle del error: {rx.respuesta_detalle}</small>}
    </div>
    <div className="c2-actions">
      {rx.direccion === "saliente" ? <Envio estado={rx.estado_envio} http={rx.envio_http} /> : <Envio estado={rx.respuesta_envio} http={rx.respuesta_http} />}
      {acciones}
    </div>
  </div>;

  return (
    <section className="corte2-shell">
      <div className="c2-title-row">
        <div><span className="eyebrow">Proyecto final</span><h2>Remisiones entre instituciones (FHIR)</h2></div>
        <div className="c2-counters">
          {role === "especialista" && <span>Recibidas pendientes <b>{entrantes.filter((x) => x.estado === "requested").length}</b></span>}
          {role === "medico" && <span>Enviadas sin respuesta <b>{salientes.filter((x) => x.estado === "requested").length}</b></span>}
          {role === "admin" && <span>Instituciones <b>{socios.length}</b></span>}
        </div>
      </div>

      {role === "medico" && <div className="grid two">
        <Card title="Consentimiento del paciente por institución">
          {!selectedPatient && <p className="muted">Selecciona un paciente.</p>}
          {selectedPatient && <div className="c2-list">
            {instituciones.map((i) => {
              const c = consentimientoDe(i.codigo);
              return <div className="c2-row" key={i.codigo}>
                <div><strong>{i.nombre}</strong><small>{c ? `Autorizado el ${new Date(c.registrado_en).toLocaleString("es-CO")}` : "Sin autorización para compartir"}</small></div>
                {c ? <button onClick={() => revocar(c)} disabled={busy}>Revocar</button> : <button onClick={() => otorgar(i.codigo)} disabled={busy}>Registrar consentimiento</button>}
              </div>;
            })}
            {!instituciones.length && <p className="muted">El administrador aún no ha registrado instituciones socias.</p>}
          </div>}
        </Card>
        <Card title="Remitir a otra institución">
          <form onSubmit={remitir} className="form-grid">
            <label className="field"><span>Institución</span><select value={form.institucion} onChange={(e) => setForm({ ...form, institucion: e.target.value })}>
              {instituciones.map((i) => <option key={i.codigo} value={i.codigo}>{i.nombre}{i.lista_para_enviar ? "" : " (sin configurar)"}</option>)}
            </select></label>
            <label className="field"><span>Especialidad</span><input list="interop-especialidades" value={form.especialidad} onChange={(e) => setForm({ ...form, especialidad: e.target.value })} /></label>
            <datalist id="interop-especialidades">{ESPECIALIDADES.map((x) => <option key={x} value={x} />)}</datalist>
            <label className="field"><span>Prioridad</span><select value={form.prioridad} onChange={(e) => setForm({ ...form, prioridad: e.target.value })}>{PRIORIDADES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
            <label className="field"><span>Motivo</span><input value={form.motivo} onChange={(e) => setForm({ ...form, motivo: e.target.value })} /></label>
            <button className="primary" disabled={busy || !selectedPatient || !form.institucion}>Remitir (Bundle FHIR)</button>
          </form>
          <p className="muted">Se envían Patient (sin teléfono, correo ni dirección), ServiceRequest, los últimos resultados con LOINC, el reporte de IA aprobado y el Consent. Sin consentimiento o sin reporte aprobado no sale nada.</p>
        </Card>
      </div>}

      {role === "medico" && <Card title={`Mis remisiones a otras instituciones (${salientes.filter((rx) => !esPrueba(rx)).length})`}>
        <div className="c2-list">
          {salientes.filter((rx) => !esPrueba(rx)).map((rx) => filaRemision(rx, <>
            <button onClick={() => verBundle(rx)}>Ver Bundle</button>
            {rx.estado_envio === "fallida" && <button onClick={() => reenviar(rx)} disabled={busy}>Reenviar</button>}
          </>))}
          {!salientes.length && <p className="muted">Aún no ha remitido pacientes a otras instituciones.</p>}
        </div>
      </Card>}

      {role === "especialista" && <Card title={`Remisiones de otras instituciones (${entrantes.length})`}>
        <div className="c2-list">
          {entrantes.filter((rx) => !esPrueba(rx)).map((rx) => filaRemision(rx, <>
            {rx.estado === "requested" && <><button className="primary" onClick={() => aceptar(rx)} disabled={busy}>Aceptar</button><button onClick={() => rechazar(rx)} disabled={busy}>Rechazar</button></>}
            {rx.estado !== "requested" && rx.respuesta_envio === "fallida" && <button onClick={() => reenviar(rx)} disabled={busy}>Reenviar respuesta</button>}
          </>))}
          {!entrantes.length && <p className="muted">Sin remisiones externas. Llegan aquí en vivo.</p>}
        </div>
      </Card>}

      {role === "admin" && <>
        <Card title="Instituciones socias">
          <div className="c2-list">
            {socios.filter((s) => verInactivas || s.estado === "activo").map((s) => <div className="c2-row" key={s.codigo}>
              <div>
                <strong>{s.codigo} · {s.nombre} · {s.estado}{s.es_espejo ? " · espejo" : ""}</strong>
                <small>Entra con {s.entrada.client_id.slice(0, 16)}… · firma {s.entrada.exige_firma ? "sí" : "no"} · salimos con {s.salida.auth}{s.salida.firma ? " + firma" : ""} · {s.estado !== "activo" ? "inactiva: no entra ni sale nada" : s.lista_para_enviar ? "lista para enviar" : "falta URL o credencial de salida"}</small>
                <label className="field"><span>URL base (con /api si aplica)</span><input value={urls[s.codigo] ?? ""} onChange={(e) => setUrls({ ...urls, [s.codigo]: e.target.value })} /></label>
              </div>
              <div className="c2-actions">
                <button onClick={() => guardarUrl(s)} disabled={busy}>Guardar URL</button>
                <button onClick={() => setSalida({ ...salida, codigo: s.codigo })}>Credencial de salida</button>
                <button onClick={() => rotar(s)} disabled={busy}>Rotar secreto</button>
                <button onClick={() => cambiarEstado(s)} disabled={busy}>{s.estado === "activo" ? "Desactivar" : "Activar"}</button>
              </div>
            </div>)}
            {!socios.length && <p className="muted">Sin instituciones. Cree la del socio o el espejo para probar.</p>}
          </div>
          <div className="c2-actions">
            <button onClick={espejo} disabled={busy}>Crear / renovar espejo</button>
            {socios.some((s) => s.estado !== "activo") && <button onClick={() => setVerInactivas(!verInactivas)}>
              {verInactivas ? "Ocultar inactivas" : `Ver inactivas (${socios.filter((s) => s.estado !== "activo").length})`}</button>}
          </div>
        </Card>
        <div className="grid two">
          <Card title="Nueva institución (ellos → nosotros)">
            <form onSubmit={crearSocio} className="form-grid" autoComplete="off">
              <label className="field"><span>Código</span><input value={nuevo.codigo} placeholder="pulso" onChange={(e) => setNuevo({ ...nuevo, codigo: e.target.value })} /></label>
              <label className="field"><span>Nombre</span><input value={nuevo.nombre} placeholder="Pulso · Hospital San Rafael" onChange={(e) => setNuevo({ ...nuevo, nombre: e.target.value })} /></label>
              <label className="field"><span>Su URL base</span><input value={nuevo.url_base} placeholder="https://xxxx.trycloudflare.com/api" onChange={(e) => setNuevo({ ...nuevo, url_base: e.target.value })} /></label>
              <label className="field"><span>Exigirles firma HMAC</span><select value={nuevo.con_firma ? "si" : "no"} onChange={(e) => setNuevo({ ...nuevo, con_firma: e.target.value === "si" })}><option value="no">No</option><option value="si">Sí</option></select></label>
              <button className="primary" disabled={busy}>Crear y generar su credencial</button>
            </form>
            {credenciales && <div className="c2-notice">
              <strong>Credenciales de {credenciales.codigo} (se muestran una sola vez; entréguelas por un canal privado):</strong>
              <pre className="json-box">{JSON.stringify({ ...credenciales, codigo: undefined }, null, 2)}</pre>
              <button onClick={() => setCredenciales(null)}>Ya las copié</button>
            </div>}
          </Card>
          <Card title="Credencial que nos dieron (nosotros → ellos)">
            <form onSubmit={guardarSalida} className="form-grid" autoComplete="off">
              <label className="field"><span>Institución</span><select value={salida.codigo} onChange={(e) => setSalida({ ...salida, codigo: e.target.value })}><option value="">Seleccionar</option>{socios.filter((s) => s.estado === "activo").map((s) => <option key={s.codigo} value={s.codigo}>{s.codigo} · {s.nombre}</option>)}</select></label>
              <label className="field"><span>Cómo entramos</span><select value={salida.auth} onChange={(e) => setSalida({ ...salida, auth: e.target.value })}><option value="oauth2">OAuth2 client credentials</option><option value="api_key">API key</option><option value="bearer">Bearer fijo</option><option value="ninguno">Sin credencial</option></select></label>
              {salida.auth === "oauth2" && <label className="field"><span>client_id</span><input name="socio-client-id" autoComplete="off" value={salida.credencial_id} onChange={(e) => setSalida({ ...salida, credencial_id: e.target.value })} /></label>}
              {salida.auth !== "ninguno" && <label className="field"><span>{salida.auth === "oauth2" ? "client_secret" : salida.auth === "api_key" ? "API key completa" : "Token"}</span><input type="password" name="socio-client-secret" autoComplete="new-password" value={salida.credencial_secreto} onChange={(e) => setSalida({ ...salida, credencial_secreto: e.target.value })} /></label>}
              <label className="field"><span>Secreto de firma (solo si la acordaron)</span><input type="password" name="socio-firma" autoComplete="new-password" value={salida.firma_secreto} onChange={(e) => setSalida({ ...salida, firma_secreto: e.target.value })} /></label>
              <button className="primary" disabled={busy || !salida.codigo}>Guardar (cifrada)</button>
            </form>
          </Card>
        </div>
        <Card title={`Intercambios con otras instituciones (${todas.length})`}>
          <div className="c2-list">{todas.filter((rx) => !esPrueba(rx)).slice(0, 30).map((rx) => filaRemision(rx, <button onClick={() => verBundle(rx)}>Ver Bundle</button>))}{!todas.length && <p className="muted">Sin intercambios todavía.</p>}</div>
        </Card>
      </>}

      {detalle && <Card title={`Bundle de ${detalle.codigo} (${detalle.direccion})`}>
        <pre className="json-box">{JSON.stringify(detalle.bundle, null, 2)}</pre>
        <button onClick={() => setDetalle(null)}>Cerrar</button>
      </Card>}
    </section>
  );
}
