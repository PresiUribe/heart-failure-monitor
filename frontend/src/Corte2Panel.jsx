import { useEffect, useMemo, useState } from "react";
import { API_URL, api, apiBlob, getToken } from "./api";

const roleOf = (me) => {
  const code = (me?.role?.code || "").toUpperCase();
  if (["MEDICO", "MEDICO_RURAL"].includes(code)) return "medico";
  if (["PACIENTE", "PACIENTE_CUIDADOR"].includes(code)) return "paciente";
  if (code === "ESPECIALISTA") return "especialista";
  if (code === "CONTABLE") return "contable";
  if (code === "ADMIN") return "admin";
  return code.toLowerCase();
};

function SmallCard({ title, children, className = "" }) {
  return <section className={`card corte2-card ${className}`}><h2>{title}</h2>{children}</section>;
}

function Notice({ children, kind = "info" }) {
  return <div className={`c2-notice ${kind}`}>{children}</div>;
}

export default function Corte2Panel({ me, selectedPatientId, patients = [], users = [], onMessage }) {
  const role = roleOf(me);
  const clinical = ["medico", "especialista", "admin"].includes(role);
  const canAnalyze = ["medico", "especialista"].includes(role);
  const [notifications, setNotifications] = useState([]);
  const [pending, setPending] = useState({ reportes_por_revisar: 0, remisiones_recibidas: 0, alertas_activas: 0 });
  const [events, setEvents] = useState([]);
  const [images, setImages] = useState([]);
  const [imageUrls, setImageUrls] = useState({});
  const [reports, setReports] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [referrals, setReferrals] = useState([]);
  const [specialists, setSpecialists] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [clusters, setClusters] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [activeAlerts, setActiveAlerts] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [adminUsers, setAdminUsers] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const [aiForm, setAiForm] = useState({
    age: 72,
    anaemia: 1,
    creatinine_phosphokinase: 250,
    diabetes: 1,
    ejection_fraction: 20,
    high_blood_pressure: 1,
    platelets: 250000,
    serum_creatinine: 2.3,
    serum_sodium: 126,
    sex: 1,
    smoking: 0,
    time: 30
  });
  const [referralForm, setReferralForm] = useState({ specialist_id: "", reason: "Valoración por cardiología por riesgo de descompensación." });
  const [imageForm, setImageForm] = useState({ source_subject_id: "patient0001", view: "4CH", phase: "ED", file: null });

  const selectedPatient = useMemo(
    () => patients.find((p) => p.id === selectedPatientId),
    [patients, selectedPatientId]
  );

  function fail(e) {
    setError(e?.message || String(e));
    if (onMessage) onMessage("", e?.message || String(e));
  }
  function success(message) {
    setError("");
    if (onMessage) onMessage(message, "");
  }
  async function run(fn) {
    try { setBusy(true); setError(""); return await fn(); }
    catch (e) { fail(e); }
    finally { setBusy(false); }
  }

  async function loadNotifications() {
    const [n, p] = await Promise.all([api("/notifications"), api("/pending")]);
    setNotifications(n); setPending(p);
    // Las alertas críticas se cargan siempre (no dependen del paciente abierto)
    // para mostrarlas como aviso persistente hasta que se reconozcan (R20).
    if (canAnalyze || role === "admin") {
      const al = await api("/alerts").catch(() => []);
      setActiveAlerts(al.filter((a) => a.estado === "activa"));
    }
    if (role === "admin") {
      const [logs, us] = await Promise.all([
        api("/audit/logs?limit=60").catch(() => []),
        api("/users?include_deleted=true").catch(() => []),
      ]);
      setAuditLogs(logs); setAdminUsers(us);
    }
  }
  async function loadClinicalModules() {
    if (!clinical || !selectedPatientId) return;
    const [imgs, reps, al, refs, specs, cls, met] = await Promise.all([
      api(`/patients/${selectedPatientId}/images`).catch(() => []),
      api(`/patients/${selectedPatientId}/ai/reports`).catch(() => []),
      api("/alerts").catch(() => []),
      api("/referrals").catch(() => []),
      api("/directory/specialists").catch(() => []),
      api("/clusters").catch(() => []),
      api("/ml/metrics").catch(() => null),
    ]);
    setImages(imgs); setReports(reps); setAlerts(al); setReferrals(refs); setSpecialists(specs); setClusters(cls); setMetrics(met);
    if (!referralForm.specialist_id && specs.length) setReferralForm((x) => ({ ...x, specialist_id: specs[0].id }));
  }
  async function loadInvoices() {
    if (!["contable", "admin"].includes(role)) return;
    setInvoices(await api("/billing/invoices"));
  }

  useEffect(() => {
    if (!me) return;
    loadNotifications().catch(fail);
    loadInvoices().catch(fail);
  }, [me?.id]);

  useEffect(() => {
    loadClinicalModules().catch(fail);
  }, [selectedPatientId, role]);

  // R17: canal en tiempo real por WebSocket con reconexión automática.
  // (Los Quick Tunnels de Cloudflare no soportan SSE; /events/stream sigue disponible en local.)
  useEffect(() => {
    if (!me || !getToken()) return;
    const base = API_URL.startsWith("http")
      ? API_URL.replace(/^http/, "ws")
      : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${API_URL}`;
    const url = `${base}/events/ws?token=${encodeURIComponent(getToken())}`;
    let socket = null;
    let retry = null;
    let closed = false;
    const connect = () => {
      socket = new WebSocket(url);
      socket.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.tipo === "keepalive") return;
          setEvents((old) => [data, ...old].slice(0, 15));
          if (data.tipo === "conexion") return;
          loadNotifications().catch(() => {});
          loadInvoices().catch(() => {});
          if (clinical) loadClinicalModules().catch(() => {});
        } catch {}
      };
      socket.onclose = () => { if (!closed) retry = setTimeout(connect, 3000); };
      socket.onerror = () => { try { socket.close(); } catch {} };
    };
    connect();
    return () => { closed = true; clearTimeout(retry); try { socket && socket.close(); } catch {} };
  }, [me?.id, selectedPatientId, role]);

  useEffect(() => () => {
    Object.values(imageUrls).forEach((url) => URL.revokeObjectURL(url));
  }, []);

  async function viewImage(image) {
    await run(async () => {
      const blob = await apiBlob(`/images/${image.id}`);
      const url = URL.createObjectURL(blob);
      setImageUrls((old) => ({ ...old, [image.id]: url }));
    });
  }

  async function uploadImage(e) {
    e.preventDefault();
    if (!selectedPatientId || !imageForm.file) return fail(new Error("Selecciona un paciente y una imagen real del dataset."));
    await run(async () => {
      const fd = new FormData();
      fd.append("file", imageForm.file);
      fd.append("source_subject_id", imageForm.source_subject_id);
      fd.append("dataset", "CAMUS");
      fd.append("view", imageForm.view);
      fd.append("phase", imageForm.phase);
      await api(`/patients/${selectedPatientId}/images/import`, { method: "POST", body: fd });
      await loadClinicalModules();
      success("Imagen CAMUS vinculada al paciente académico y almacenada en PACS.");
    });
  }

  async function analyze(e) {
    e.preventDefault();
    if (!selectedPatientId) return fail(new Error("Selecciona un paciente."));
    await run(async () => {
      const payload = Object.fromEntries(Object.entries(aiForm).map(([k, v]) => [k, Number(v)]));
      const result = await api(`/patients/${selectedPatientId}/ai/analyze`, { method: "POST", body: JSON.stringify(payload) });
      await Promise.all([loadClinicalModules(), loadNotifications()]);
      success(`Reporte IA creado en estado pendiente. Riesgo ${(result.risk_score * 100).toFixed(1)}%.`);
    });
  }

  async function review(reportId, action) {
    await run(async () => {
      const obs = window.prompt(action === "approve" ? "Observación clínica para aprobar:" : "Motivo/observación del rechazo:", "Revisión académica realizada.");
      if (obs === null) return;
      await api(`/ai/reports/${reportId}/${action}`, { method: "POST", body: JSON.stringify({ observations: obs }) });
      await Promise.all([loadClinicalModules(), loadNotifications(), loadInvoices()]);
      success(action === "approve" ? "Reporte aprobado; la factura se creó automáticamente." : "Reporte rechazado y conservado como evidencia.");
    });
  }

  async function acknowledge(alertId) {
    await run(async () => {
      const note = window.prompt("Nota de reconocimiento de la alerta:", "Paciente revisado por el clínico.");
      if (!note) return;
      await api(`/alerts/${alertId}/acknowledge`, { method: "POST", body: JSON.stringify({ note }) });
      setActiveAlerts((old) => old.filter((a) => a.id !== alertId));
      await Promise.all([loadClinicalModules(), loadNotifications()]);
      success("Alerta crítica reconocida.");
    });
  }

  async function createReferral(e) {
    e.preventDefault();
    if (!selectedPatientId || !referralForm.specialist_id) return fail(new Error("Selecciona paciente y especialista."));
    await run(async () => {
      await api(`/patients/${selectedPatientId}/referrals`, { method: "POST", body: JSON.stringify(referralForm) });
      await Promise.all([loadClinicalModules(), loadNotifications()]);
      success("Remisión creada; el especialista recibió la notificación en vivo.");
    });
  }

  async function acceptReferral(id) {
    await run(async () => {
      await api(`/referrals/${id}/accept`, { method: "POST" });
      await Promise.all([loadClinicalModules(), loadNotifications()]);
      success("Remisión aceptada; el médico remitente fue notificado.");
    });
  }

  async function unlockUser(id) {
    await run(async () => {
      await api(`/users/${id}/unlock`, { method: "POST" });
      await loadNotifications();
      success("Cuenta desbloqueada; el evento quedó en la auditoría.");
    });
  }

  async function markRead(id) {
    await run(async () => {
      await api(`/notifications/${id}/read`, { method: "POST" });
      await loadNotifications();
    });
  }

  async function downloadHistory() {
    if (!selectedPatientId) return;
    await run(async () => {
      const blob = await apiBlob(`/patients/${selectedPatientId}/history.pdf`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `historia_${selectedPatient?.patient_code || selectedPatientId}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  const lockedUsers = adminUsers.filter((u) => u.is_locked);

  return (
    <section className="corte2-shell">
      <div className="c2-title-row">
        <div><span className="eyebrow">Corte 2</span><h2>Sistema clínico activo</h2></div>
        <div className="c2-counters">
          <span>IA pendientes <b>{pending.reportes_por_revisar}</b></span>
          <span>Remisiones <b>{pending.remisiones_recibidas}</b></span>
          <span>Alertas <b>{pending.alertas_activas}</b></span>
        </div>
      </div>
      {error && <Notice kind="error">{error}</Notice>}

      {activeAlerts.length > 0 && <div className="c2-alert-banner" role="alert">
        <strong>⚠ {activeAlerts.length} alerta(s) crítica(s) sin reconocer</strong>
        {activeAlerts.map((a) => <div className="c2-alert-item" key={a.id}>
          <span>{a.mensaje} · paciente {a.paciente_id.slice(0, 8)}{a.escalated ? " · ESCALADA al admin" : ""}</span>
          {canAnalyze && <button onClick={() => acknowledge(a.id)}>Reconocer</button>}
        </div>)}
      </div>}

      <div className="grid two">
        <SmallCard title={`Bandeja de notificaciones (${notifications.filter((x) => !x.leida).length} nuevas)`}>
          <div className="c2-list">
            {notifications.slice(0, 8).map((n) => <div className={`c2-row ${n.leida ? "read" : ""}`} key={n.id}>
              <div><strong>{n.tipo}</strong><span>{n.mensaje}</span><small>{n.fecha ? new Date(n.fecha).toLocaleString("es-CO") : ""}</small></div>
              {!n.leida && <button onClick={() => markRead(n.id)}>Marcar leída</button>}
            </div>)}
            {!notifications.length && <p className="muted">Sin notificaciones.</p>}
          </div>
        </SmallCard>
        <SmallCard title="Eventos en vivo (WebSocket)">
          <div className="c2-list live-events">
            {events.map((e, i) => <div className="c2-row" key={`${e.tipo}-${i}`}><div><strong>{e.tipo}</strong><span>{e.mensaje}</span></div></div>)}
            {!events.length && <p className="muted">Canal conectado. Los eventos aparecerán sin recargar la página.</p>}
          </div>
        </SmallCard>
      </div>

      {["contable", "admin"].includes(role) && <SmallCard title={`Contabilidad MongoDB (${invoices.length})`}>
        <div className="c2-table-wrap"><table><thead><tr><th>Servicio</th><th>Paciente</th><th>Valor</th><th>Estado</th><th>Reporte IA</th></tr></thead><tbody>
          {invoices.map((x) => <tr key={x.id}><td>{x.servicio}</td><td>{x.paciente_id || x.patient_id}</td><td>${Number(x.valor || 0).toLocaleString("es-CO")}</td><td>{x.estado_pago}</td><td>{x.reporte_id || "Manual"}</td></tr>)}
        </tbody></table></div>
      </SmallCard>}

      {role === "admin" && <div className="grid two">
        <SmallCard title={`Cuentas bloqueadas (${lockedUsers.length})`}>
          <div className="c2-list">
            {lockedUsers.map((u) => <div className="c2-row" key={u.id}><div><strong>{u.username}</strong><span>{u.role?.code} · {u.failed_login_attempts} intentos fallidos</span></div><button onClick={() => unlockUser(u.id)}>Desbloquear</button></div>)}
            {!lockedUsers.length && <p className="muted">No hay cuentas bloqueadas.</p>}
          </div>
        </SmallCard>
        <SmallCard title={`Auditoría (últimos ${auditLogs.length})`}>
          <div className="c2-table-wrap"><table><thead><tr><th>Fecha</th><th>Acción</th><th>Recurso</th><th>Usuario</th></tr></thead><tbody>
            {auditLogs.map((l) => <tr key={l.id}><td>{new Date(l.created_at).toLocaleTimeString("es-CO")}</td><td>{l.action}</td><td>{l.entity_type} {l.entity_id ? l.entity_id.slice(0, 8) : ""}</td><td>{l.user_id ? l.user_id.slice(0, 8) : "sistema"}</td></tr>)}
          </tbody></table></div>
        </SmallCard>
      </div>}

      {clinical && selectedPatientId && <>
        <SmallCard title={`Ficha activa · ${selectedPatient?.patient_code || selectedPatientId}`}>
          <div className="c2-actions"><button onClick={downloadHistory}>Descargar historia clínica PDF</button><span className="muted">Los reportes IA pendientes/rechazados no se incluyen.</span></div>
        </SmallCard>

        <div className="grid two">
          <SmallCard title="PACS · Ecocardiografías CAMUS">
            <Notice>Asociación académica: la imagen real CAMUS se vincula al paciente creado para demostrar el flujo; no se afirma identidad clínica entre ambas fuentes.</Notice>
            <form onSubmit={uploadImage} className="form-grid">
              <label className="field"><span>Imagen PNG/JPG/DICOM real</span><input type="file" accept="image/png,image/jpeg,.dcm" onChange={(e) => setImageForm({ ...imageForm, file: e.target.files?.[0] || null })} /></label>
              <label className="field"><span>ID original CAMUS</span><input value={imageForm.source_subject_id} onChange={(e) => setImageForm({ ...imageForm, source_subject_id: e.target.value })} /></label>
              <label className="field"><span>Vista</span><select value={imageForm.view} onChange={(e) => setImageForm({ ...imageForm, view: e.target.value })}><option>4CH</option><option>2CH</option></select></label>
              <label className="field"><span>Fase</span><select value={imageForm.phase} onChange={(e) => setImageForm({ ...imageForm, phase: e.target.value })}><option>ED</option><option>ES</option></select></label>
              <button className="primary" disabled={busy}>Importar a Orthanc</button>
            </form>
            <div className="image-grid">
              {images.map((img) => <div className="image-tile" key={img.id}><div><b>{img.dataset}</b> · {img.view || "US"} · {img.phase || ""}</div><small>{img.source_subject_id}</small>{imageUrls[img.id] ? <img src={imageUrls[img.id]} alt={`Ecocardiografía ${img.source_subject_id}`} /> : <button onClick={() => viewImage(img)}>Ver imagen</button>}</div>)}
              {!images.length && <p className="muted">Aún no hay imágenes asociadas a este paciente.</p>}
            </div>
          </SmallCard>

          <SmallCard title="IA supervisada · Heart Failure UCI">
            <form onSubmit={analyze} className="compact-grid">
              {[
                ["age", "Edad"], ["ejection_fraction", "Fracción eyección (%)"], ["serum_creatinine", "Creatinina"],
                ["serum_sodium", "Sodio"], ["platelets", "Plaquetas"], ["creatinine_phosphokinase", "CPK"],
                ["anaemia", "Anemia 0/1"], ["diabetes", "Diabetes 0/1"], ["high_blood_pressure", "HTA 0/1"],
                ["sex", "Sexo 0/1"], ["smoking", "Fuma 0/1"], ["time", "Seguimiento días"]
              ].map(([key, label]) => <label className="field" key={key}><span>{label}</span><input type="number" step="any" value={aiForm[key]} onChange={(e) => setAiForm({ ...aiForm, [key]: e.target.value })} /></label>)}
              <button className="primary" disabled={!canAnalyze || busy}>{canAnalyze ? "Ejecutar análisis" : "Solo médico/especialista ejecuta IA"}</button>
            </form>
            <div className="c2-list">
              {reports.map((r) => <div className="c2-row" key={r.id}><div><strong>{r.nivel} · {(Number(r.risk_score) * 100).toFixed(1)}%</strong><span>Estado: {r.estado}</span>{r.observaciones && <small>{r.observaciones}</small>}</div>{r.estado === "pendiente" && canAnalyze && <div className="c2-actions"><button onClick={() => review(r.id, "approve")}>Aprobar</button><button onClick={() => review(r.id, "reject")}>Rechazar</button></div>}</div>)}
            </div>
          </SmallCard>
        </div>

        <div className="grid two">
          <SmallCard title="Alertas críticas persistentes">
            <div className="c2-list">{alerts.map((a) => <div className={`c2-row alert-row ${a.estado}`} key={a.id}><div><strong>{a.nivel}</strong><span>{a.mensaje}</span>{a.escalated && <small>Escalada por falta de reconocimiento</small>}</div>{a.estado === "activa" && canAnalyze && <button onClick={() => acknowledge(a.id)}>Reconocer</button>}</div>)}{!alerts.length && <p className="muted">Sin alertas.</p>}</div>
          </SmallCard>

          <SmallCard title="Remisión a especialista">
            {role !== "especialista" && <form onSubmit={createReferral} className="form-grid">
              <label className="field"><span>Especialista</span><select value={referralForm.specialist_id} onChange={(e) => setReferralForm({ ...referralForm, specialist_id: e.target.value })}><option value="">Seleccionar</option>{specialists.map((s) => <option key={s.id} value={s.id}>{s.user_code} · {s.full_name}</option>)}</select></label>
              <label className="field"><span>Motivo</span><input value={referralForm.reason} onChange={(e) => setReferralForm({ ...referralForm, reason: e.target.value })} /></label>
              <button className="primary">Remitir</button>
            </form>}
            <div className="c2-list">{referrals.map((r) => <div className="c2-row" key={r.id}><div><strong>{r.estado}</strong><span>{r.motivo}</span></div>{role === "especialista" && r.estado === "pendiente" && <button onClick={() => acceptReferral(r.id)}>Aceptar</button>}</div>)}</div>
          </SmallCard>
        </div>

        <div className="grid two">
          <SmallCard title="Clusters UCI + aumento sintético">
            <div className="c2-list">{clusters.map((c) => <div className="c2-row" key={c.cluster}><div><strong>Cluster {c.cluster}</strong><span>{c.descripcion}</span><small>Reales: {c.n_reales} · Sintéticos: {c.n_sinteticos}</small></div></div>)}</div>
          </SmallCard>
          <SmallCard title="Métricas del modelo">
            {metrics ? <pre className="json-box">{JSON.stringify(metrics, null, 2)}</pre> : <p className="muted">Sin métricas disponibles.</p>}
          </SmallCard>
        </div>
      </>}
    </section>
  );
}
