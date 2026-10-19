BEGIN;

CREATE SEQUENCE IF NOT EXISTS user_code_seq START WITH 1;
CREATE SEQUENCE IF NOT EXISTS patient_code_seq START WITH 1;

ALTER TABLE users ADD COLUMN IF NOT EXISTS user_code VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name VARCHAR(150);
ALTER TABLE users ALTER COLUMN user_code SET DEFAULT ('USR-' || LPAD(nextval('user_code_seq')::text, 6, '0'));
UPDATE users
SET user_code = 'USR-' || LPAD(nextval('user_code_seq')::text, 6, '0')
WHERE user_code IS NULL;
ALTER TABLE users ALTER COLUMN user_code SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_user_code ON users(user_code);

ALTER TABLE patients ADD COLUMN IF NOT EXISTS patient_code VARCHAR(20);
ALTER TABLE patients ALTER COLUMN patient_code SET DEFAULT ('PAT-' || LPAD(nextval('patient_code_seq')::text, 6, '0'));
UPDATE patients
SET patient_code = 'PAT-' || LPAD(nextval('patient_code_seq')::text, 6, '0')
WHERE patient_code IS NULL;
ALTER TABLE patients ALTER COLUMN patient_code SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_patients_patient_code ON patients(patient_code);

COMMIT;
