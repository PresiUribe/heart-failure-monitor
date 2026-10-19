# Identificadores: UUID interno + documento para Swagger

## Decisión de diseño

El sistema conserva `users.id` y `patients.id` como UUID técnicos internos. Las
relaciones de PostgreSQL, auditoría, MongoDB y seguridad siguen usando esos UUID.

Para las consultas manuales y la interoperabilidad, las rutas aceptan el número
de identificación como identificador humano preferido. También se mantienen
compatibles los UUID y los códigos `USR-*` / `PAT-*`.

## Login

El inicio de sesión pide exclusivamente:

- `username`
- `password`

No se usa la cédula para autenticarse.

## Ejemplos Swagger

```text
GET  /users/1000000002
POST /users/1000000002/unlock
POST /users/1000000002/reset-password

GET  /patients/DEMO-HF-001
GET  /patients/DEMO-HF-001/images
GET  /patients/DEMO-HF-001/history
POST /patients/DEMO-HF-001/ai/analyze
```

Para un paciente con cédula numérica, se usa directamente esa cédula.

## FHIR

El documento es el identificador administrativo/interoperable. El UUID no se
reemplaza: continúa siendo la llave técnica. En recursos FHIR, la cédula debe
representarse dentro de `identifier`, no como sustituto obligatorio del `id` del
recurso.

## IA y privacidad

El servicio de IA no recibe el documento. Recibe un seudónimo derivado del UUID
interno, preservando la minimización de datos.
