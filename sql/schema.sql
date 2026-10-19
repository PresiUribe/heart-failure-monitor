CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SEQUENCE IF NOT EXISTS user_code_seq START WITH 1;
CREATE SEQUENCE IF NOT EXISTS patient_code_seq START WITH 1;

CREATE TABLE IF NOT EXISTS roles (
    id SMALLSERIAL PRIMARY KEY,
    code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    description VARCHAR(255),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO roles (code,name,description) VALUES
('ADMIN','Administrador','Gestión completa, restauración, desbloqueo y auditoría'),
('MEDICO','Médico','Médico general/rural: pacientes, IA, reportes y remisiones'),
('ESPECIALISTA','Especialista','Recibe remisiones y atiende pacientes remitidos'),
('PACIENTE','Paciente','Consulta únicamente su propia información'),
('CONTABLE','Contable','Gestiona facturación sin acceso a datos clínicos'),
('MEDICO_RURAL','Médico Rural (legado)','Alias legado del Corte 1'),
('PACIENTE_CUIDADOR','Paciente/Cuidador (legado)','Alias legado del Corte 1')
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_code VARCHAR(20) NOT NULL UNIQUE DEFAULT ('USR-' || LPAD(nextval('user_code_seq')::text,6,'0')),
    document_type VARCHAR(10) NOT NULL DEFAULT 'CC',
    document_number VARCHAR(50) NOT NULL UNIQUE,
    full_name VARCHAR(150),
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(150) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role_id SMALLINT NOT NULL REFERENCES roles(id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    failed_login_attempts SMALLINT NOT NULL DEFAULT 0,
    is_locked BOOLEAN NOT NULL DEFAULT FALSE,
    locked_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL,
    deleted_by UUID NULL REFERENCES users(id),
    CONSTRAINT chk_users_document_type CHECK (document_type IN ('CC','CE','TI','PA','PPT','RC'))
);

CREATE TABLE IF NOT EXISTS patients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_code VARCHAR(20) NOT NULL UNIQUE DEFAULT ('PAT-' || LPAD(nextval('patient_code_seq')::text,6,'0')),
    document_number VARCHAR(50) NOT NULL UNIQUE,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    birth_date DATE,
    sex VARCHAR(20),
    municipality VARCHAR(120),
    rural_area VARCHAR(150),
    primary_diagnosis VARCHAR(255),
    fhir_patient_id VARCHAR(100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL,
    deleted_by UUID NULL REFERENCES users(id),
    CONSTRAINT chk_patient_sex CHECK (sex IS NULL OR sex IN ('MALE','FEMALE','OTHER','UNKNOWN'))
);

CREATE TABLE IF NOT EXISTS patient_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id),
    user_id UUID NOT NULL REFERENCES users(id),
    relationship_type VARCHAR(50) NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT chk_relationship_type CHECK (relationship_type IN ('PACIENTE','CUIDADOR','MEDICO_RURAL','ESPECIALISTA')),
    CONSTRAINT uq_patient_user_relationship UNIQUE(patient_id,user_id,relationship_type)
);

CREATE TABLE IF NOT EXISTS encounters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id),
    created_by UUID NOT NULL REFERENCES users(id),
    encounter_type VARCHAR(50) NOT NULL,
    reason VARCHAR(255),
    clinical_notes TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'IN_PROGRESS',
    start_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_at TIMESTAMP NULL,
    fhir_encounter_id VARCHAR(100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL,
    deleted_by UUID NULL REFERENCES users(id),
    CONSTRAINT chk_encounter_type CHECK (encounter_type IN ('CONSULTA_RURAL','CONTROL','TELECONSULTA','REMISION','URGENCIA')),
    CONSTRAINT chk_encounter_status CHECK (status IN ('PLANNED','IN_PROGRESS','FINISHED','CANCELLED'))
);

CREATE TABLE IF NOT EXISTS observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id),
    encounter_id UUID NULL REFERENCES encounters(id),
    created_by UUID NOT NULL REFERENCES users(id),
    code_system VARCHAR(255), code VARCHAR(100), display VARCHAR(255),
    value_type VARCHAR(30) NOT NULL,
    value_numeric NUMERIC(12,3), value_text TEXT, value_boolean BOOLEAN,
    unit VARCHAR(50), measured_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fhir_observation_id VARCHAR(100), is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL,
    deleted_by UUID NULL REFERENCES users(id),
    CONSTRAINT chk_observation_value_type CHECK (value_type IN ('NUMERIC','TEXT','BOOLEAN','COMPONENTS'))
);

CREATE TABLE IF NOT EXISTS observation_components (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_id UUID NOT NULL REFERENCES observations(id),
    code_system VARCHAR(255), code VARCHAR(100) NOT NULL, display VARCHAR(255),
    value_numeric NUMERIC(12,3) NOT NULL, unit VARCHAR(50),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS record_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), entity_type VARCHAR(100) NOT NULL,
    entity_id UUID NOT NULL, previous_data JSONB, new_data JSONB,
    changed_by UUID NOT NULL REFERENCES users(id), change_type VARCHAR(30) NOT NULL,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_change_type CHECK (change_type IN ('SOFT_EDIT','SOFT_DELETE','RESTORE'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id UUID NULL REFERENCES users(id),
    action VARCHAR(80) NOT NULL, entity_type VARCHAR(100) NOT NULL,
    entity_id UUID NULL, details JSONB, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id UUID NOT NULL REFERENCES users(id),
    tipo VARCHAR(80) NOT NULL, mensaje VARCHAR(500) NOT NULL, leida BOOLEAN NOT NULL DEFAULT FALSE,
    patient_id UUID NULL REFERENCES patients(id), data JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS referrals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), patient_id UUID NOT NULL REFERENCES patients(id),
    referred_by UUID NOT NULL REFERENCES users(id), specialist_id UUID NOT NULL REFERENCES users(id),
    reason TEXT NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    fhir_service_request_id VARCHAR(100), created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    accepted_at TIMESTAMP NULL,
    CONSTRAINT chk_referral_status CHECK (status IN ('PENDING','ACCEPTED','REJECTED'))
);

CREATE TABLE IF NOT EXISTS ai_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), patient_id UUID NOT NULL REFERENCES patients(id),
    created_by UUID NOT NULL REFERENCES users(id), status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    model_name VARCHAR(120) NOT NULL, risk_score DOUBLE PRECISION NOT NULL,
    level VARCHAR(30) NOT NULL, result JSONB NOT NULL, clinical_notes TEXT,
    reviewed_by UUID NULL REFERENCES users(id), reviewed_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_ai_report_status CHECK (status IN ('PENDING','APPROVED','REJECTED'))
);

CREATE TABLE IF NOT EXISTS ml_payloads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), report_id UUID NOT NULL UNIQUE REFERENCES ai_reports(id),
    pseudonym VARCHAR(80) NOT NULL, payload JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), patient_id UUID NOT NULL REFERENCES patients(id),
    report_id UUID NULL REFERENCES ai_reports(id), assigned_to UUID NOT NULL REFERENCES users(id),
    level VARCHAR(30) NOT NULL DEFAULT 'CRITICAL', state VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    message VARCHAR(500) NOT NULL, acknowledged_by UUID NULL REFERENCES users(id),
    acknowledgment_note TEXT, acknowledged_at TIMESTAMP NULL,
    escalated BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_alert_state CHECK (state IN ('ACTIVE','ACKNOWLEDGED'))
);

CREATE TABLE IF NOT EXISTS imaging_studies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), patient_id UUID NOT NULL REFERENCES patients(id),
    dataset VARCHAR(100) NOT NULL DEFAULT 'CAMUS', source_subject_id VARCHAR(100) NOT NULL,
    modality VARCHAR(20) NOT NULL DEFAULT 'US', view VARCHAR(20), phase VARCHAR(20),
    orthanc_instance_id VARCHAR(120) NOT NULL, academic_link BOOLEAN NOT NULL DEFAULT TRUE,
    source_license VARCHAR(120), created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role_id);
CREATE INDEX IF NOT EXISTS idx_users_locked ON users(is_locked) WHERE is_locked;
CREATE INDEX IF NOT EXISTS idx_assignments_patient ON patient_assignments(patient_id);
CREATE INDEX IF NOT EXISTS idx_assignments_user ON patient_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_encounters_patient ON encounters(patient_id);
CREATE INDEX IF NOT EXISTS idx_observations_patient ON observations(patient_id);
CREATE INDEX IF NOT EXISTS idx_observations_code ON observations(code);
CREATE INDEX IF NOT EXISTS idx_observations_measured_at ON observations(measured_at);
CREATE INDEX IF NOT EXISTS idx_history_entity ON record_history(entity_type,entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_referrals_specialist ON referrals(specialist_id,status);
CREATE INDEX IF NOT EXISTS idx_ai_reports_patient ON ai_reports(patient_id,status);
CREATE INDEX IF NOT EXISTS idx_alerts_assigned ON alerts(assigned_to,state);
CREATE INDEX IF NOT EXISTS idx_images_patient ON imaging_studies(patient_id);
CREATE INDEX IF NOT EXISTS idx_patients_fhir_id ON patients(fhir_patient_id);
CREATE INDEX IF NOT EXISTS idx_encounters_fhir_id ON encounters(fhir_encounter_id);
CREATE INDEX IF NOT EXISTS idx_observations_fhir_id ON observations(fhir_observation_id);
CREATE INDEX IF NOT EXISTS idx_users_user_code ON users(user_code);
CREATE INDEX IF NOT EXISTS idx_patients_patient_code ON patients(patient_code);
