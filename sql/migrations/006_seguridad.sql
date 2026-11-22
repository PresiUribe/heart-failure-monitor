-- Proyecto final · Seguridad: R27 (cifrado de campos) y R28 (auditoría encadenada).
-- Idempotente: la API lo aplica sola al arrancar (app/interop/esquema.py), y luego, en Python
-- (porque necesita la llave), cifra los datos existentes y sella la auditoría previa.

-- R27: documento, teléfono, dirección y vereda se guardan cifrados (texto "gcm1:..."); el documento
-- se busca y se mantiene único por su índice ciego (HMAC), no por el valor.
DO $$
BEGIN
    IF (SELECT data_type FROM information_schema.columns
        WHERE table_name = 'patients' AND column_name = 'document_number') <> 'text' THEN
        ALTER TABLE patients ALTER COLUMN document_number TYPE TEXT;
    END IF;
    IF (SELECT data_type FROM information_schema.columns
        WHERE table_name = 'patients' AND column_name = 'rural_area') <> 'text' THEN
        ALTER TABLE patients ALTER COLUMN rural_area TYPE TEXT;
    END IF;
END $$;
ALTER TABLE patients DROP CONSTRAINT IF EXISTS patients_document_number_key;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS document_hash VARCHAR(64);
ALTER TABLE patients ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS address TEXT;
-- (el índice único de document_hash se crea después de calcularlo para los pacientes existentes)

-- R28: cadena de hashes en la auditoría.
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS seq BIGINT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS prev_hash VARCHAR(64);
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS hash VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_log_seq ON audit_log(seq);

-- Capa 1: un registro sellado no se puede modificar ni borrar desde la aplicación.
-- (Capa 2: si un dueño de la base desactiva el disparador y lo altera, GET /audit/verificar lo detecta.)
CREATE OR REPLACE FUNCTION audit_log_solo_agregar() RETURNS trigger AS $$
BEGIN
    IF OLD.hash IS NOT NULL THEN
        RAISE EXCEPTION USING MESSAGE = 'audit_log es de solo agregar (R28): el registro ' || OLD.seq || ' ya está sellado';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_audit_log_solo_agregar ON audit_log;
CREATE TRIGGER trg_audit_log_solo_agregar BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_solo_agregar();

-- R25: intentos de prompt injection (quién, desde qué IP, qué patrón) y bloqueo temporal.
CREATE TABLE IF NOT EXISTS agente_incidentes (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    usuario VARCHAR(100),
    rol VARCHAR(30),
    ip VARCHAR(64),
    origen VARCHAR(20) NOT NULL,          -- pregunta (directa) | herramienta (indirecta: dato con instrucciones)
    patrones TEXT,
    extracto TEXT,                         -- ya seudonimizado
    liberado BOOLEAN NOT NULL DEFAULT FALSE,
    creado TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agente_incidentes_usuario ON agente_incidentes(user_id, creado);
CREATE INDEX IF NOT EXISTS idx_agente_incidentes_ip ON agente_incidentes(ip, creado);
