BEGIN;

INSERT INTO roles (code,name,description) VALUES
('MEDICO','Médico','Médico general/rural: pacientes, IA, reportes y remisiones'),
('PACIENTE','Paciente','Consulta únicamente su propia información'),
('CONTABLE','Contable','Gestiona facturación sin acceso a datos clínicos')
ON CONFLICT (code) DO NOTHING;

ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_attempts SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_locked BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_at TIMESTAMP NULL;
-- Compatibilidad temporal con bases heredadas. La migración 004 vuelve a
-- establecer document_number como identificador humano obligatorio para usuarios.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='users' AND column_name='document_number'
  ) THEN
    ALTER TABLE users ALTER COLUMN document_number DROP NOT NULL;
  END IF;
END $$;
ALTER TABLE audit_log ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE audit_log ALTER COLUMN action TYPE VARCHAR(80);

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

CREATE INDEX IF NOT EXISTS idx_users_locked ON users(is_locked) WHERE is_locked;
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_referrals_specialist ON referrals(specialist_id,status);
CREATE INDEX IF NOT EXISTS idx_ai_reports_patient ON ai_reports(patient_id,status);
CREATE INDEX IF NOT EXISTS idx_alerts_assigned ON alerts(assigned_to,state);
CREATE INDEX IF NOT EXISTS idx_images_patient ON imaging_studies(patient_id);
COMMIT;
