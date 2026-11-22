# Mapa académico de controles técnicos y normativa colombiana

> Este documento se prepara para la sustentación académica y no sustituye concepto jurídico. La rúbrica solicita expresamente mapear Ley 1581/2012, Ley 2015/2020, Resolución 866/2021, Resolución 1995/1999 y Resolución 3100/2019. A octubre de 2026, la Resolución 3100 de 2019 fue actualizada/derogada por la Resolución 1732 de 2026; se mantiene aquí porque forma parte explícita del requisito docente.

| Norma / referencia | Principio o disposición usada en el proyecto | Control técnico implementado |
|---|---|---|
| Ley 1581 de 2012, art. 4 | Principios de finalidad, acceso/circulación restringida, seguridad y confidencialidad | RBAC por rol, aislamiento de pacientes, CORS, JWT, hash Argon2, rate limit, auditoría, minimización y seudonimización del payload IA |
| Ley 1581 de 2012, deberes asociados al tratamiento | Protección de información sensible y trazabilidad | No se envía identidad civil al servicio ML; payload exacto consultable; logs de acciones relevantes |
| Ley 2015 de 2020, arts. 3–5 | Interoperabilidad, administración del modelo, guarda/custodia y reserva | HAPI FHIR R4, recursos Patient/Encounter/Observation, acceso autenticado, separación de persistencias, historia clínica PDF y auditoría |
| Resolución 866 de 2021, art. 10 y anexo técnico | Disposición/intercambio de datos clínicos relevantes con seguridad y circulación restringida | FHIR con terminologías/códigos, Subscription rest-hook, minimización, notificación reactiva cuando llega Observation externa |
| Resolución 1995 de 1999, manejo/custodia y reserva de historia clínica | Integridad, disponibilidad, acceso autorizado y trazabilidad de la historia | Soft delete, restauración solo admin, record_history, audit_log, PDF de historia, reportes IA rechazados conservados fuera de la historia clínica aprobada |
| Resolución 3100 de 2019, Manual: estándar de historia clínica y registros | Existencia y manejo de registros clínicos dentro de estándares de habilitación | Historia clínica estructurada, controles de acceso, registros de atención, observaciones, trazabilidad y disponibilidad del documento PDF |

## Decisiones de diseño relacionadas

- `contable` no puede consultar historia clínica, imágenes ni datos clínicos; opera únicamente facturación.
- `paciente` solo puede consultar su propia información mediante una asignación explícita.
- los resultados de IA no se vuelven parte de la historia clínica hasta que un médico o especialista los aprueba;
- las imágenes CAMUS conservan procedencia y una bandera de asociación académica;
- los eventos críticos permanecen activos hasta reconocimiento clínico y quedan auditados;
- la interoperabilidad FHIR reacciona a eventos externos en lugar de depender de sondeo ciego.

## Fuentes normativas de referencia

- Ministerio de Salud — marco IHCE: https://www.minsalud.gov.co/ihce/Paginas/Normatividad.aspx
- Ley 2015 de 2020: https://www.minsalud.gov.co/sites/rid/Lists/BibliotecaDigital/RIDE/INEC/IGUB/ley-2015-de-2020.pdf
- Resolución 866 de 2021: https://www.minsalud.gov.co/sites/rid/Lists/BibliotecaDigital/RIDE/DE/DIJ/resolucion-866-de-2021.pdf
- Resolución 1995 de 1999: portal de normatividad del Ministerio de Salud.

## Controles del proyecto final (R23–R33)

| Norma / referencia | Principio | Control técnico implementado | Requisito |
|---|---|---|---|
| Ley 1581 de 2012, art. 4 (seguridad, confidencialidad) | Medidas técnicas que impidan el acceso no autorizado | Cifrado AES-256-GCM de documento, teléfono, dirección y vereda, con índice ciego para buscar; llave en variable de entorno | R27 |
| Ley 1581 de 2012, arts. 4 y 17 (transferencia de datos sensibles) | Autorización previa y finalidad | Consent registrado por paciente **y por institución** destino; sin él no sale nada (409); revocable | R32 |
| Ley 1581 de 2012, art. 4 (finalidad, minimización) | Solo lo necesario | Bundle sin teléfono, correo ni dirección; solo el reporte de IA aprobado; el LLM recibe solo el seudónimo PSN- | R24, R32 |
| Ley 1581 de 2012 (deber de demostrar cumplimiento) | Trazabilidad | Auditoría del payload exacto enviado al LLM y de cada herramienta del agente | R24 |
| Resolución 1995 de 1999 (integridad y custodia de la historia) | La historia no se altera sin dejar rastro | Auditoría con cadena de hashes HMAC, disparador de solo agregar y verificación por el admin | R28 |
| Resolución 1995 de 1999 (reserva) | Acceso solo a quien corresponde | Herramientas del agente con permisos por rol validados en el backend; defensa contra prompt injection con registro y bloqueo temporal | R24, R25 |
| Resolución 866 de 2021 (intercambio de datos clínicos) | Interoperabilidad con estándares | Remisiones en FHIR R4 (Bundle, ServiceRequest, Observation LOINC/UCUM, DiagnosticReport, Consent, Task, SNOMED CT); errores como OperationOutcome | R30 |
| Resolución 866 de 2021 / Ley 2015 de 2020 | Intercambio seguro entre instituciones | Credenciales de sistema por institución (OAuth2 o API key) que no abren la API clínica; firma HMAC opcional; webhook FHIR con secreto | R29, R31 |
| Resolución 3100 de 2019 (registros y disponibilidad) | Registros completos y disponibles | Remisiones externas con estado, intentos y reenvío; consulta de estado por el socio (`GET /interop/Task/{id}`) | R30, R33 |
| Guías de práctica clínica (pertinencia) | Decisión apoyada en evidencia | Agente RAG que cita documento y fragmento, y se abstiene cuando no hay soporte | R23 |

El modelo de amenazas que justifica cada control está en `docs/STRIDE.md`.
