BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS document_type VARCHAR(10) DEFAULT 'CC';
ALTER TABLE users ADD COLUMN IF NOT EXISTS document_number VARCHAR(50);

UPDATE users SET document_type = 'CC' WHERE document_type IS NULL OR btrim(document_type) = '';
UPDATE users
SET document_number = 'LEGACY-' || COALESCE(user_code, substr(id::text, 1, 8))
WHERE document_number IS NULL OR btrim(document_number) = '';

ALTER TABLE users ALTER COLUMN document_type SET NOT NULL;
ALTER TABLE users ALTER COLUMN document_number SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_document_number ON users(document_number);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_users_document_type') THEN
    ALTER TABLE users ADD CONSTRAINT chk_users_document_type
      CHECK (document_type IN ('CC','CE','TI','PA','PPT','RC'));
  END IF;
END $$;

COMMIT;
