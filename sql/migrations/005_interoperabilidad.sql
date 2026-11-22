-- Proyecto final · Interoperabilidad entre instituciones (R29–R33).
-- Idempotente: la API lo aplica sola al arrancar (app/interop/esquema.py).
-- También se puede aplicar a mano:
--   Get-Content -Raw .\sql\migrations\005_interoperabilidad.sql | docker compose exec -T app-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

-- Pacientes: tipo de documento (viaja en el Patient), id federado y origen externo.
ALTER TABLE patients ADD COLUMN IF NOT EXISTS document_type VARCHAR(10) NOT NULL DEFAULT 'CC';
ALTER TABLE patients ADD COLUMN IF NOT EXISTS sid_federado VARCHAR(32);
ALTER TABLE patients ADD COLUMN IF NOT EXISTS origen_institucion VARCHAR(60);
CREATE INDEX IF NOT EXISTS idx_patients_sid_federado ON patients(sid_federado);

-- Observaciones: de dónde vino el dato (NULL = registrada aquí).
ALTER TABLE observations ADD COLUMN IF NOT EXISTS origen VARCHAR(80);

-- Remisiones: motivo del rechazo y fecha de la respuesta.
ALTER TABLE referrals ADD COLUMN IF NOT EXISTS response_note TEXT;
ALTER TABLE referrals ADD COLUMN IF NOT EXISTS responded_at TIMESTAMP;

-- Instituciones socias: cómo entran ellas (credencial que les dimos) y cómo entramos nosotros (la que nos dieron).
CREATE TABLE IF NOT EXISTS interop_socios (
    id SERIAL PRIMARY KEY,
    codigo VARCHAR(60) NOT NULL UNIQUE,
    nombre VARCHAR(150) NOT NULL,
    url_base VARCHAR(300),
    estado VARCHAR(20) NOT NULL DEFAULT 'activo',
    es_espejo BOOLEAN NOT NULL DEFAULT FALSE,
    -- Entrada (ellos -> nosotros)
    client_id VARCHAR(60) NOT NULL UNIQUE,
    secreto_hash VARCHAR(255) NOT NULL,
    scopes VARCHAR(200) NOT NULL DEFAULT 'system/ServiceRequest.write system/Task.write',
    exige_firma BOOLEAN NOT NULL DEFAULT FALSE,
    firma_secreto_cif TEXT,
    -- Salida (nosotros -> ellos)
    auth_salida VARCHAR(20) NOT NULL DEFAULT 'ninguno',
    ruta_token VARCHAR(200) NOT NULL DEFAULT '/oauth/token',
    ruta_bundle VARCHAR(200) NOT NULL DEFAULT '/interop/Bundle',
    ruta_task VARCHAR(200) NOT NULL DEFAULT '/interop/Task',
    cabecera_api_key VARCHAR(80) NOT NULL DEFAULT 'X-API-Key',
    scope_salida VARCHAR(200),
    cred_id_salida VARCHAR(200),
    cred_secreto_salida_cif TEXT,
    firma_salida BOOLEAN NOT NULL DEFAULT FALSE,
    firma_secreto_salida_cif TEXT,
    creado TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_socio_estado CHECK (estado IN ('activo','inactivo')),
    CONSTRAINT chk_socio_auth CHECK (auth_salida IN ('oauth2','api_key','bearer','ninguno'))
);

-- Nonces de un solo uso por socio (anti-replay). Se purgan a las 24 h.
CREATE TABLE IF NOT EXISTS interop_nonces (
    socio_id INTEGER NOT NULL REFERENCES interop_socios(id) ON DELETE CASCADE,
    nonce VARCHAR(128) NOT NULL,
    creado TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (socio_id, nonce)
);

-- Consentimiento del paciente para compartir con UNA institución (Ley 1581 de 2012).
CREATE TABLE IF NOT EXISTS consentimientos (
    id SERIAL PRIMARY KEY,
    patient_id UUID NOT NULL REFERENCES patients(id),
    socio_id INTEGER NOT NULL REFERENCES interop_socios(id),
    estado VARCHAR(20) NOT NULL DEFAULT 'activo',
    finalidad VARCHAR(20) NOT NULL DEFAULT 'TREAT',
    nota TEXT,
    registrado_por UUID REFERENCES users(id),
    registrado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    revocado_por UUID REFERENCES users(id),
    revocado_en TIMESTAMPTZ,
    CONSTRAINT chk_consentimiento_estado CHECK (estado IN ('activo','revocado'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_consentimiento_activo ON consentimientos(patient_id, socio_id) WHERE estado = 'activo';

-- Remisiones que cruzan la frontera: entrantes (nos remiten) y salientes (remitimos).
CREATE TABLE IF NOT EXISTS remisiones_externas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    numero BIGSERIAL NOT NULL UNIQUE,
    direccion VARCHAR(10) NOT NULL,
    socio_id INTEGER NOT NULL REFERENCES interop_socios(id),
    patient_id UUID NOT NULL REFERENCES patients(id),
    referral_id UUID REFERENCES referrals(id),
    medico_id UUID REFERENCES users(id),
    estado VARCHAR(20) NOT NULL DEFAULT 'requested',
    prioridad VARCHAR(10) NOT NULL DEFAULT 'routine',
    especialidad VARCHAR(120),
    motivo TEXT NOT NULL,
    -- Entrantes: identificadores del socio, para no duplicar y para responderle.
    clave_externa VARCHAR(300),
    sr_id_externo VARCHAR(200),
    sr_identifier JSONB,
    task_id_externo VARCHAR(200),
    patient_ref_externo VARCHAR(200),
    reporte JSONB,
    consentimiento JSONB,
    observaciones INTEGER NOT NULL DEFAULT 0,
    bundle JSONB,
    -- Salientes: resultado del envío.
    estado_envio VARCHAR(20),
    envio_http INTEGER,
    envio_detalle TEXT,
    id_remoto VARCHAR(200),
    intentos INTEGER NOT NULL DEFAULT 0,
    -- Respuesta (motivo; y para entrantes, si nuestro Task llegó al socio).
    respuesta_motivo TEXT,
    respuesta_envio VARCHAR(20),
    respuesta_http INTEGER,
    respuesta_detalle TEXT,
    creado TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_rx_direccion CHECK (direccion IN ('entrante','saliente')),
    CONSTRAINT chk_rx_estado CHECK (estado IN ('requested','accepted','rejected','in-progress','completed','cancelled','failed')),
    CONSTRAINT chk_rx_prioridad CHECK (prioridad IN ('routine','urgent','asap','stat'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_rx_entrante ON remisiones_externas(socio_id, clave_externa) WHERE direccion = 'entrante';
CREATE INDEX IF NOT EXISTS idx_rx_referral ON remisiones_externas(referral_id);
CREATE INDEX IF NOT EXISTS idx_rx_medico ON remisiones_externas(medico_id);

-- Corrección: el usuario técnico tenía un correo de dominio reservado que rompía GET /users.
UPDATE users SET email = 'sistema.interop@hfmonitor.uao.edu.co'
WHERE username = 'sistema.interop' AND email = 'sistema.interop@invalid.local';
