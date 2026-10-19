import { useEffect, useMemo, useState } from "react";
import { api, clearToken, getToken, login } from "./api";
import Corte2Panel from "./Corte2Panel";

function Card({ title, children, className = "" }) {
  return <section className={`card ${className}`}><h2>{title}</h2>{children}</section>;
}
function Field({ label, ...props }) {
  return <label className="field"><span>{label}</span><input {...props} /></label>;
}
function Select({ label, children, ...props }) {
  return <label className="field"><span>{label}</span><select {...props}>{children}</select></label>;
}
function Status({ message, error }) {
  if (!message && !error) return null;
  return <div className={error ? "status error" : "status ok"}>{error || message}</div>;
}
function JsonBox({ value }) {
  if (!value) return null;
  return <pre className="json-box">{JSON.stringify(value, null, 2)}</pre>;
}
function formatDate(value) {
  if (!value) return "Sin registro";
  return new Date(value).toLocaleString("es-CO");
}

function PatientOnlyProfile({ me, onLogout }) {
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/patient-profile/me").then(setProfile).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="patient-shell">
      <header className="patient-header">
        <div><span className="eyebrow">Mi seguimiento</span><h1>Perfil del paciente</h1></div>
        <div className="patient-user"><strong>{me.full_name || me.username}</strong><span>{me.user_code}</span><button onClick={onLogout}>Cerrar sesión</button></div>
      </header>
      {error && <Status error={error} />}
      {!profile ? <Card title="Cargando"><p className="muted">Consultando información clínica...</p></Card> : (
        <>
          <Card title={`${profile.patient_code} · ${profile.full_name}`}>
            <p className="muted">Información clínica de solo lectura</p>
          </Card>
          <div className="profile-grid">
            <Card title="Médico que lo atendió" className="profile-card">
              {profile.doctor ? <>
                <div className="metric-main">{profile.doctor.name}</div>
                <div className="metric-detail">{profile.doctor.user_code} · {profile.doctor.role}</div>
                <div className="metric-detail">{profile.doctor.encounter_type}</div>
                <div className="metric-date">{formatDate(profile.doctor.attended_at)}</div>
              </> : <p className="muted">Aún no hay una atención clínica registrada.</p>}
            </Card>
            <Card title="Último peso" className="profile-card">
              {profile.weight ? <>
                <div className="metric-number">{profile.weight.value} <small>{profile.weight.unit || "kg"}</small></div>
                <div className="metric-date">{formatDate(profile.weight.measured_at)}</div>
              </> : <p className="muted">Sin registro de peso.</p>}
            </Card>
            <Card title="Última presión arterial" className="profile-card">
              {profile.blood_pressure ? <>
                <div className="metric-number">{profile.blood_pressure.systolic ?? "--"}/{profile.blood_pressure.diastolic ?? "--"} <small>mmHg</small></div>
                <div className="metric-date">{formatDate(profile.blood_pressure.measured_at)}</div>
              </> : <p className="muted">Sin registro de presión arterial.</p>}
            </Card>
          </div>
          <div className="read-only-note">Perfil de solo lectura. La información clínica es registrada por el personal autorizado.</div>
        </>
      )}
    </div>
  );
}

export default function App() {
  const [tokenPresent, setTokenPresent] = useState(Boolean(getToken()));
  const [me, setMe] = useState(null);
  const [patients, setPatients] = useState([]);
  const [allPatients, setAllPatients] = useState([]);
  const [users, setUsers] = useState([]);
  const [allUsers, setAllUsers] = useState([]);
  const [selectedPatientId, setSelectedPatientId] = useState("");
  const [encounters, setEncounters] = useState([]);
  const [observations, setObservations] = useState([]);
  const [loginForm, setLoginForm] = useState({ username: "admin.demo", password: "AdminDemo2026!" });
  const [patientForm, setPatientForm] = useState({
    document_number: "1001001001", first_name: "Juan", last_name: "Perez",
    birth_date: "1962-04-15", sex: "MALE", municipality: "Cali",
    rural_area: "Zona rural", primary_diagnosis: "Insuficiencia cardiaca"
  });
  const [encounterForm, setEncounterForm] = useState({
    encounter_type: "CONSULTA_RURAL", reason: "Control de insuficiencia cardiaca",
    clinical_notes: "Paciente inicia seguimiento remoto.", status: "FINISHED"
  });
  const [observationForm, setObservationForm] = useState({
    code_system: "http://loinc.org", code: "29463-7", display: "Body weight",
    value_type: "NUMERIC", value_numeric: "82.5", unit: "kg"
  });
  const [bpForm, setBpForm] = useState({ systolic: "142", diastolic: "90" });
  const [userForm, setUserForm] = useState({
    document_type: "CC", document_number: "1002003004",
    full_name: "", username: "medico.rural", email: "medico@saludrural.com",
    password: "Medico123!", role_code: "MEDICO"
  });
  const [assignmentForm, setAssignmentForm] = useState({ user_id: "", relationship_type: "MEDICO_RURAL", is_primary: true });
  const [resetPasswordForm, setResetPasswordForm] = useState({ user_id: "", new_password: "", confirm_password: "" });
  const [statusMessage, setStatusMessage] = useState("");
  const [error, setError] = useState("");
  const [lastResponse, setLastResponse] = useState(null);
  const [busy, setBusy] = useState(false);

  const isAdmin = me?.role?.code === "ADMIN";
  const isPatient = ["PACIENTE", "PACIENTE_CUIDADOR"].includes(me?.role?.code);
  const isContable = me?.role?.code === "CONTABLE";
  const isMedico = ["MEDICO", "MEDICO_RURAL"].includes(me?.role?.code);
  const selectedPatient = useMemo(() => patients.find((p) => p.id === selectedPatientId), [patients, selectedPatientId]);
  const selectedAssignmentUser = useMemo(() => users.find((u) => u.id === assignmentForm.user_id), [users, assignmentForm.user_id]);
  const activeEncounters = encounters.filter((x) => x.is_active);
  const deletedEncounters = encounters.filter((x) => !x.is_active);
  const activeObservations = observations.filter((x) => x.is_active);
  const deletedObservations = observations.filter((x) => !x.is_active);
  const deletedPatients = allPatients.filter((x) => !x.is_active);
  const deletedUsers = allUsers.filter((x) => !x.is_active);

  function showSuccess(message, response = null) { setError(""); setStatusMessage(message); setLastResponse(response); }
  function showError(err) { setStatusMessage(""); setError(err?.message || String(err)); }
  async function run(action) { try { setBusy(true); setError(""); return await action(); } catch (e) { showError(e); } finally { setBusy(false); } }

  async function loadMe() {
    if (!getToken()) return;
    try { const data = await api("/users/me"); setMe(data); setTokenPresent(true); }
    catch { clearToken(); setTokenPresent(false); setMe(null); }
  }
  async function loadPatients() {
    const data = await api("/patients"); setPatients(data);
    if (!selectedPatientId && data.length) setSelectedPatientId(data[0].id);
    if (isAdmin) setAllPatients(await api("/patients?include_deleted=true"));
    return data;
  }
  async function loadUsers() {
    if (!isAdmin) return;
    const active = await api("/users");
    const all = await api("/users?include_deleted=true");
    setUsers(active); setAllUsers(all);
    if (!assignmentForm.user_id && active.length) chooseAssignmentUser(active[0].id, active);
    if (!resetPasswordForm.user_id && active.length) {
      setResetPasswordForm((p) => ({ ...p, user_id: active[0].id }));
    }
  }
  async function loadClinical(patientId = selectedPatientId) {
    if (!patientId) return;
    const suffix = isAdmin ? "?include_deleted=true" : "";
    const [enc, obs] = await Promise.all([
      api(`/patients/${patientId}/encounters${suffix}`),
      api(`/patients/${patientId}/observations${suffix}`)
    ]);
    setEncounters(enc); setObservations(obs);
  }
  function chooseAssignmentUser(userId, sourceUsers = users) {
    const user = sourceUsers.find((u) => u.id === userId);
    let relationship = "MEDICO_RURAL";
    if (user?.role?.code === "ESPECIALISTA") relationship = "ESPECIALISTA";
    if (["PACIENTE", "PACIENTE_CUIDADOR"].includes(user?.role?.code)) relationship = "PACIENTE";
    if (user?.role?.code === "MEDICO") relationship = "MEDICO_RURAL";
    setAssignmentForm((p) => ({ ...p, user_id: userId, relationship_type: relationship }));
  }

  useEffect(() => { loadMe(); }, []);
  useEffect(() => {
    if (!me || isPatient || isContable) return;
    loadPatients().catch(showError);
    if (isAdmin) loadUsers().catch(showError);
  }, [me]);
  useEffect(() => {
    if (!selectedPatientId || isPatient || isContable) return;
    loadClinical(selectedPatientId).catch(showError);
  }, [selectedPatientId, isAdmin]);

  async function handleLogin(e) { e.preventDefault(); await run(async () => { const data = await login(loginForm.username, loginForm.password); setTokenPresent(true); await loadMe(); showSuccess("Sesión iniciada.", data); }); }
  function handleLogout() { clearToken(); setTokenPresent(false); setMe(null); setPatients([]); setUsers([]); setSelectedPatientId(""); setEncounters([]); setObservations([]); }
  async function createUser(e) { e.preventDefault(); await run(async () => { const data = await api("/users", { method: "POST", body: JSON.stringify(userForm) }); await loadUsers(); chooseAssignmentUser(data.id, [data, ...users]); showSuccess(`Usuario ${data.user_code} creado.`, data); }); }
  async function resetPassword(e) {
    e.preventDefault();

    if (!resetPasswordForm.user_id) {
      return showError(new Error("Selecciona un usuario."));
    }

    if (resetPasswordForm.new_password.length < 8) {
      return showError(new Error("La nueva contraseña debe tener al menos 8 caracteres."));
    }

    if (resetPasswordForm.new_password !== resetPasswordForm.confirm_password) {
      return showError(new Error("Las contraseñas no coinciden."));
    }

    await run(async () => {
      const target = users.find((u) => u.id === resetPasswordForm.user_id);
      const data = await api(`/users/${resetPasswordForm.user_id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ new_password: resetPasswordForm.new_password })
      });

      setResetPasswordForm((p) => ({
        ...p,
        new_password: "",
        confirm_password: ""
      }));

      showSuccess(
        `Contraseña restablecida para ${target?.user_code || data.user_code} · ${target?.full_name || target?.username || data.username}.`,
        data
      );
    });
  }

  async function createPatient(e) { e.preventDefault(); await run(async () => { const data = await api("/patients", { method: "POST", body: JSON.stringify({ ...patientForm, birth_date: patientForm.birth_date || null }) }); await loadPatients(); setSelectedPatientId(data.id); showSuccess(`Paciente ${data.patient_code} creado.`, data); }); }
  async function assignUser(e) { e.preventDefault(); if (!selectedPatientId || !assignmentForm.user_id) return showError(new Error("Selecciona paciente y usuario.")); await run(async () => { const data = await api(`/patients/${selectedPatientId}/assignments`, { method: "POST", body: JSON.stringify(assignmentForm) }); showSuccess("Usuario asignado al paciente.", data); }); }
  async function createEncounter(e) { e.preventDefault(); await run(async () => { const data = await api(`/patients/${selectedPatientId}/encounters`, { method: "POST", body: JSON.stringify(encounterForm) }); await loadClinical(); showSuccess("Encounter creado.", data); }); }
  async function createObservation(e) { e.preventDefault(); await run(async () => { const data = await api(`/patients/${selectedPatientId}/observations`, { method: "POST", body: JSON.stringify({ ...observationForm, value_numeric: Number(observationForm.value_numeric), encounter_id: activeEncounters[0]?.id || null, components: [] }) }); await loadClinical(); showSuccess("Observation creada.", data); }); }
  async function createBloodPressure(e) { e.preventDefault(); await run(async () => { const data = await api(`/patients/${selectedPatientId}/observations/blood-pressure`, { method: "POST", body: JSON.stringify({ encounter_id: activeEncounters[0]?.id || null, systolic: Number(bpForm.systolic), diastolic: Number(bpForm.diastolic) }) }); await loadClinical(); showSuccess("Presión arterial registrada.", data); }); }
  async function syncFhir() { await run(async () => { const data = await api(`/fhir/patients/${selectedPatientId}/sync-all`, { method: "POST" }); await loadPatients(); await loadClinical(); showSuccess("Sincronización FHIR completada.", data); }); }
  async function updateObservation(id, currentValue) { const value = window.prompt("Nuevo valor:", currentValue ?? ""); if (value === null) return; await run(async () => { const data = await api(`/observations/${id}`, { method: "PUT", body: JSON.stringify({ value_numeric: Number(value) }) }); await loadClinical(); showSuccess("Soft edit ejecutado.", data); }); }
  async function deleteObservation(id) { if (!confirm("¿Enviar esta observación a la papelera?")) return; await run(async () => { await api(`/observations/${id}`, { method: "DELETE" }); await loadClinical(); showSuccess("Observación enviada a la papelera."); }); }
  async function deleteEncounter(id) { if (!confirm("¿Enviar este encuentro a la papelera?")) return; await run(async () => { await api(`/encounters/${id}`, { method: "DELETE" }); await loadClinical(); showSuccess("Encounter enviado a la papelera."); }); }
  async function restoreObservation(id) { await run(async () => { await api(`/observations/${id}/restore`, { method: "POST" }); await loadClinical(); showSuccess("Observación restaurada."); }); }
  async function restoreEncounter(id) { await run(async () => { await api(`/encounters/${id}/restore`, { method: "POST" }); await loadClinical(); showSuccess("Encounter restaurado."); }); }
  async function restorePatient(id) { await run(async () => { await api(`/patients/${id}/restore`, { method: "POST" }); await loadPatients(); showSuccess("Paciente restaurado."); }); }
  async function restoreUser(id) { await run(async () => { await api(`/users/${id}/restore`, { method: "POST" }); await loadUsers(); showSuccess("Usuario restaurado."); }); }

  if (!tokenPresent || !me) return (
    <main className="login-shell"><div className="login-panel">
      <div><span className="eyebrow">Salud Digital · Insuficiencia cardiaca</span><h1>Monitoreo rural</h1><p>Acceso al sistema clínico interoperable.</p></div>
      <form onSubmit={handleLogin} className="form-grid">
        <Field label="Usuario" value={loginForm.username} onChange={(e) => setLoginForm({ ...loginForm, username: e.target.value })} />
        <Field label="Contraseña" type="password" value={loginForm.password} onChange={(e) => setLoginForm({ ...loginForm, password: e.target.value })} />
        <button className="primary" disabled={busy}>Ingresar</button>
      </form><Status message={statusMessage} error={error} />
    </div></main>
  );

  if (isPatient) return (
    <>
      <PatientOnlyProfile me={me} onLogout={handleLogout} />
      {/* R18: el paciente también tiene bandeja persistente y canal en vivo propio */}
      <main className="content"><Corte2Panel me={me} selectedPatientId="" patients={[]} onMessage={(ok, err) => { setStatusMessage(ok); setError(err); }} /></main>
    </>
  );
  if (isContable) return (
    <div className="app-shell">
      <header className="topbar"><div><span className="eyebrow">Salud Digital · Corte 2</span><h1>Panel contable</h1></div><div className="session-box"><strong>{me.full_name || me.username}</strong><span>{me.role.code}</span><button onClick={handleLogout}>Cerrar sesión</button></div></header>
      <main className="content"><Corte2Panel me={me} selectedPatientId="" patients={[]} onMessage={(ok, err) => { setStatusMessage(ok); setError(err); }} /><Status message={statusMessage} error={error} /></main>
    </div>
  );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div><span className="eyebrow">Salud Rural IC</span><h1>Panel clínico</h1><div className="user-box"><strong>{me.full_name || me.username}</strong><span>{me.document_number || me.user_code} · {me.role.code}</span><small>{me.email}</small></div></div>
        <div className="sidebar-actions"><button onClick={() => run(loadPatients)}>Actualizar</button><button onClick={handleLogout}>Cerrar sesión</button></div>
      </aside>
      <main className="content">
        <header className="topbar"><div><span className="eyebrow">Backend conectado</span><h1>Pruebas funcionales</h1></div><div className="topbar-status"><span className="dot" />FastAPI + PostgreSQL + HAPI FHIR + PACS</div></header>
        <Status message={statusMessage} error={error} />

        <div className="grid two">
          <Card title="Pacientes"><select className="patient-select" value={selectedPatientId} onChange={(e) => setSelectedPatientId(e.target.value)}><option value="">Seleccionar paciente</option>{patients.map((p) => <option key={p.id} value={p.id}>{p.patient_code} · {p.first_name} {p.last_name} · {p.document_number}</option>)}</select>{selectedPatient && <div className="summary"><strong>{selectedPatient.patient_code} · {selectedPatient.first_name} {selectedPatient.last_name}</strong><span>{selectedPatient.primary_diagnosis}</span><span>{selectedPatient.municipality}</span><span>FHIR: {selectedPatient.fhir_patient_id || "sin sincronizar"}</span></div>}</Card>
          <Card title="FHIR"><p className="muted">Sincroniza Patient, Encounter y Observation del paciente.</p><button className="primary" onClick={syncFhir} disabled={!selectedPatientId || busy}>Sincronizar todo con HAPI FHIR</button></Card>
        </div>

        {isAdmin && <div className="grid two">
          <Card title="Crear usuario"><form onSubmit={createUser} className="form-grid">
            <Select label="Tipo documento" value={userForm.document_type} onChange={(e) => setUserForm({ ...userForm, document_type: e.target.value })}><option value="CC">CC</option><option value="CE">CE</option><option value="TI">TI</option><option value="PA">PA</option><option value="PPT">PPT</option><option value="RC">RC</option></Select>
            <Field label="Número de identificación" value={userForm.document_number} onChange={(e) => setUserForm({ ...userForm, document_number: e.target.value })} />
            <Field label="Nombre completo" value={userForm.full_name} onChange={(e) => setUserForm({ ...userForm, full_name: e.target.value })} />
            <Field label="Usuario" value={userForm.username} onChange={(e) => setUserForm({ ...userForm, username: e.target.value })} />
            <Field label="Email" value={userForm.email} onChange={(e) => setUserForm({ ...userForm, email: e.target.value })} />
            <Field label="Contraseña" type="password" value={userForm.password} onChange={(e) => setUserForm({ ...userForm, password: e.target.value })} />
            <Select label="Rol" value={userForm.role_code} onChange={(e) => setUserForm({ ...userForm, role_code: e.target.value })}><option value="MEDICO">MEDICO</option><option value="ESPECIALISTA">ESPECIALISTA</option><option value="PACIENTE">PACIENTE</option><option value="CONTABLE">CONTABLE</option><option value="ADMIN">ADMIN</option></Select>
            <button className="primary">Crear usuario</button>
          </form></Card>
          <Card title="Asignar usuario a paciente"><form onSubmit={assignUser} className="form-grid">
            <Select label="Usuario" value={assignmentForm.user_id} onChange={(e) => chooseAssignmentUser(e.target.value)}><option value="">Seleccionar usuario</option>{users.filter((u) => u.role.code !== "ADMIN").map((u) => <option key={u.id} value={u.id}>{u.document_number} · {u.full_name || u.username} · {u.role.code}</option>)}</Select>
            {["PACIENTE", "PACIENTE_CUIDADOR"].includes(selectedAssignmentUser?.role?.code) ? <Select label="Relación" value={assignmentForm.relationship_type} onChange={(e) => setAssignmentForm({ ...assignmentForm, relationship_type: e.target.value })}><option value="PACIENTE">PACIENTE</option><option value="CUIDADOR">CUIDADOR</option></Select> : <div className="derived-role"><span>Relación automática</span><strong>{assignmentForm.relationship_type}</strong></div>}
            <button className="primary" disabled={!selectedPatientId || !assignmentForm.user_id}>Asignar</button>
          </form></Card>
        </div>}

        {isAdmin && <Card title="Restablecer contraseña">
          <p className="muted">
            El administrador no puede ver la contraseña actual. Solo puede establecer una nueva contraseña.
          </p>
          <form onSubmit={resetPassword} className="form-grid password-reset-grid">
            <Select
              label="Usuario"
              value={resetPasswordForm.user_id}
              onChange={(e) => setResetPasswordForm({ ...resetPasswordForm, user_id: e.target.value })}
            >
              <option value="">Seleccionar usuario</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.document_number} · {u.full_name || u.username} · {u.role.code}
                </option>
              ))}
            </Select>
            <Field
              label="Nueva contraseña"
              type="password"
              value={resetPasswordForm.new_password}
              onChange={(e) => setResetPasswordForm({ ...resetPasswordForm, new_password: e.target.value })}
              placeholder="Mínimo 8 caracteres"
            />
            <Field
              label="Confirmar nueva contraseña"
              type="password"
              value={resetPasswordForm.confirm_password}
              onChange={(e) => setResetPasswordForm({ ...resetPasswordForm, confirm_password: e.target.value })}
              placeholder="Repita la contraseña"
            />
            <button
              className="primary"
              disabled={
                busy ||
                !resetPasswordForm.user_id ||
                !resetPasswordForm.new_password ||
                !resetPasswordForm.confirm_password
              }
            >
              Restablecer contraseña
            </button>
          </form>
          <p className="security-note">
            La contraseña se almacena únicamente como hash Argon2. Nunca se muestra ni se guarda en texto plano.
          </p>
        </Card>}

        {(isAdmin || isMedico) && <Card title="Crear paciente"><form onSubmit={createPatient} className="form-grid columns-4">
          <Field label="Documento" value={patientForm.document_number} onChange={(e) => setPatientForm({ ...patientForm, document_number: e.target.value })} /><Field label="Nombres" value={patientForm.first_name} onChange={(e) => setPatientForm({ ...patientForm, first_name: e.target.value })} /><Field label="Apellidos" value={patientForm.last_name} onChange={(e) => setPatientForm({ ...patientForm, last_name: e.target.value })} /><Field label="Fecha nacimiento" type="date" value={patientForm.birth_date} onChange={(e) => setPatientForm({ ...patientForm, birth_date: e.target.value })} />
          <Select label="Sexo" value={patientForm.sex} onChange={(e) => setPatientForm({ ...patientForm, sex: e.target.value })}><option value="MALE">MALE</option><option value="FEMALE">FEMALE</option><option value="OTHER">OTHER</option><option value="UNKNOWN">UNKNOWN</option></Select><Field label="Municipio" value={patientForm.municipality} onChange={(e) => setPatientForm({ ...patientForm, municipality: e.target.value })} /><Field label="Zona rural" value={patientForm.rural_area} onChange={(e) => setPatientForm({ ...patientForm, rural_area: e.target.value })} /><Field label="Diagnóstico" value={patientForm.primary_diagnosis} onChange={(e) => setPatientForm({ ...patientForm, primary_diagnosis: e.target.value })} /><button className="primary">Crear paciente</button>
        </form></Card>}

        <div className="grid two">
          <Card title="Crear Encounter"><form onSubmit={createEncounter} className="form-grid"><Select label="Tipo" value={encounterForm.encounter_type} onChange={(e) => setEncounterForm({ ...encounterForm, encounter_type: e.target.value })}><option value="CONSULTA_RURAL">CONSULTA_RURAL</option><option value="CONTROL">CONTROL</option><option value="TELECONSULTA">TELECONSULTA</option><option value="REMISION">REMISION</option><option value="URGENCIA">URGENCIA</option></Select><Field label="Motivo" value={encounterForm.reason} onChange={(e) => setEncounterForm({ ...encounterForm, reason: e.target.value })} /><Field label="Nota clínica" value={encounterForm.clinical_notes} onChange={(e) => setEncounterForm({ ...encounterForm, clinical_notes: e.target.value })} /><button className="primary" disabled={!selectedPatientId}>Crear Encounter</button></form></Card>
          <Card title="Registrar Observation"><form onSubmit={createObservation} className="form-grid"><Select label="Tipo" value={observationForm.code} onChange={(e) => { const p = e.target.value === "29463-7" ? { code: "29463-7", display: "Body weight", unit: "kg", value_numeric: "82.5" } : { code: "8867-4", display: "Heart rate", unit: "beats/min", value_numeric: "92" }; setObservationForm({ ...observationForm, ...p }); }}><option value="29463-7">Peso</option><option value="8867-4">Frecuencia cardiaca</option></Select><Field label="Valor" type="number" step="0.1" value={observationForm.value_numeric} onChange={(e) => setObservationForm({ ...observationForm, value_numeric: e.target.value })} /><Field label="Unidad" value={observationForm.unit} readOnly /><button className="primary" disabled={!selectedPatientId}>Registrar</button></form></Card>
        </div>
        <Card title="Registrar presión arterial"><form onSubmit={createBloodPressure} className="inline-form"><Field label="Sistólica" type="number" value={bpForm.systolic} onChange={(e) => setBpForm({ ...bpForm, systolic: e.target.value })} /><Field label="Diastólica" type="number" value={bpForm.diastolic} onChange={(e) => setBpForm({ ...bpForm, diastolic: e.target.value })} /><button className="primary" disabled={!selectedPatientId}>Registrar presión</button></form></Card>

        <div className="grid two">
          <Card title={`Encounters activos (${activeEncounters.length})`}><div className="list">{activeEncounters.length === 0 && <p className="muted">Sin registros.</p>}{activeEncounters.map((x) => <article key={x.id} className="list-item"><strong>{x.encounter_type}</strong><span>{x.reason}</span><small>{formatDate(x.start_at)}</small><div className="actions"><button onClick={() => deleteEncounter(x.id)}>Soft delete</button></div></article>)}</div></Card>
          <Card title={`Observations activas (${activeObservations.length})`}><div className="list">{activeObservations.length === 0 && <p className="muted">Sin registros.</p>}{activeObservations.map((x) => <article key={x.id} className="list-item"><div className="row-between"><strong>{x.display || x.code}</strong><span className="pill">{x.code}</span></div>{x.value_type === "NUMERIC" && <span className="big-value">{x.value_numeric} {x.unit}</span>}{x.value_type === "COMPONENTS" && x.components?.map((c) => <span key={c.id}>{c.display}: <strong>{c.value_numeric} {c.unit}</strong></span>)}<div className="actions">{x.value_type === "NUMERIC" && <button onClick={() => updateObservation(x.id, x.value_numeric)}>Soft edit</button>}<button onClick={() => deleteObservation(x.id)}>Soft delete</button></div></article>)}</div></Card>
        </div>

        {isAdmin && <Card title="Papelera · Solo administrador"><div className="trash-grid">
          <div><h3>Observaciones eliminadas</h3>{deletedObservations.length === 0 ? <p className="muted">Vacía</p> : deletedObservations.map((x) => <div className="trash-item" key={x.id}><span>{x.display || x.code} · {formatDate(x.deleted_at)}</span><button onClick={() => restoreObservation(x.id)}>Restaurar</button></div>)}</div>
          <div><h3>Encuentros eliminados</h3>{deletedEncounters.length === 0 ? <p className="muted">Vacía</p> : deletedEncounters.map((x) => <div className="trash-item" key={x.id}><span>{x.encounter_type} · {formatDate(x.deleted_at)}</span><button onClick={() => restoreEncounter(x.id)}>Restaurar</button></div>)}</div>
          <div><h3>Pacientes eliminados</h3>{deletedPatients.length === 0 ? <p className="muted">Vacía</p> : deletedPatients.map((x) => <div className="trash-item" key={x.id}><span>{x.patient_code} · {x.first_name} {x.last_name}</span><button onClick={() => restorePatient(x.id)}>Restaurar</button></div>)}</div>
          <div><h3>Usuarios eliminados</h3>{deletedUsers.length === 0 ? <p className="muted">Vacía</p> : deletedUsers.map((x) => <div className="trash-item" key={x.id}><span>{x.user_code} · {x.full_name || x.username}</span><button onClick={() => restoreUser(x.id)}>Restaurar</button></div>)}</div>
        </div></Card>}

        <Corte2Panel me={me} selectedPatientId={selectedPatientId} patients={patients} users={users} onMessage={(ok, err) => { setStatusMessage(ok); setError(err); }} />

        <Card title="Última respuesta"><JsonBox value={lastResponse} /></Card>
      </main>
    </div>
  );
}
