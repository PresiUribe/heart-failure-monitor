# Mapa de implementación — Corte 2

| Req. | Implementación |
|---|---|
| R01 | Roles `ADMIN`, `MEDICO`, `ESPECIALISTA`, `PACIENTE`, `CONTABLE`; `GET /yo`; solo admin crea usuarios. |
| R02 | Bloqueo al tercer fallo; 423 mientras está bloqueado; desbloqueo admin; auditoría y notificación al admin. |
| R03 | JWT, RBAC, aislamiento por asignación, Argon2, CORS restringido, headers, rate limiting Nginx/API, `.env` ignorado. |
| R04 | `cloudflared` vive en Docker Compose y publica Nginx/SPA/API/FHIR por HTTPS Quick Tunnel. |
| R05 | Compose incluye PostgreSQL, MongoDB, HAPI FHIR, Orthanc, ML, FastAPI, React/Nginx y cloudflared. |
| R06 | CAMUS real (patient0001: 2CH/4CH, ED/ES) → `camus_to_png.py` → endpoint de importación → DICOM → Orthanc → visor autenticado en la ficha. |
| R07 | 187 registros reales UCI + K-Means (k=5 por silueta y codo) + sintéticos generados dentro de cada cluster y marcados; interpretación clínica y zonas de confusión en `DATASETS.md`. |
| R08 | Remisiones médico→especialista, bandeja, aceptación, auditoría, aviso en vivo y reflejo FHIR `ServiceRequest`. |
| R09 | Facturación MongoDB, acceso contable/admin, factura automática al aprobar IA. |
| R10 | IA crea `PENDING`; médico/especialista aprueba/rechaza; solo aprobados entran a historia. |
| R11 | Historia JSON + PDF; solo reportes IA aprobados. |
| R12 | Payload minimizado/seudonimizado y persistencia exacta en `ml_payloads`. |
| R13 | `audit_log` cubre seguridad, clínica, IA, remisiones, facturas, alertas, FHIR, soft delete/restore. |
| R14 | El médico envía a la papelera los pacientes que registró; solo el admin restaura. |
| R15 | El paciente se sincroniza a HAPI al crearse (identificadores: documento, UUID y código PAT); Encounter y Observation con códigos LOINC. |
| R16 | React SPA con módulos Corte 2, actualización por WebSocket sin recarga, banda persistente de alertas y panel admin (desbloqueo y auditoría). |
| R17 | WebSocket `/events/ws?token=` autenticado (canal principal, porque los Quick Tunnels de Cloudflare no soportan SSE) y SSE `/events/stream?token=` como alternativa; eventos por usuario; Nginx con `Upgrade`/`Connection` y `proxy_buffering off`. |
| R18 | `notifications` persistentes + `/pending`. |
| R19 | Reactores: remitir→notificar, aceptar→notificar, aprobar IA→facturar/notificar contable, bloquear→notificar admin. |
| R20 | Alertas activas, reconocimiento con nota, auditoría y escalamiento automático. |
| R21 | HAPI REST-hook Subscription creada por FastAPI al iniciar; callback `/fhir/events/observation`. Además, el POST público exacto `/fhir/Observation` pasa por un gateway no-polling que primero persiste en HAPI y dispara el mismo reactor, evitando demoras del rest-hook durante el tester. |
| R22 | README (arquitectura, diagramas, despliegue), DATASETS, CONTABILIDAD, EVENTOS, SEGURIDAD_IA, MODELO_ML (métricas, umbral, subgrupos) y MAPA_NORMATIVO. |

Verificación automática de R01–R21 con `scripts/e2e_check.py` contra la URL pública.