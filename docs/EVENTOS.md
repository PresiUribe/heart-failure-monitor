# Catálogo de eventos reactivos

El sistema tiene dos canales en tiempo real sobre el mismo bus de eventos (`app/services/event_bus.py`), con el mismo alcance por usuario:

- **WebSocket** `GET /events/ws?token=...` (público: `wss://<url>/api/events/ws?token=...`). Es el **canal principal** y el que usa el frontend. Los Quick Tunnels de Cloudflare no soportan Server-Sent Events ([documentación oficial](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)): los eventos quedaban retenidos en el túnel. WebSocket sí atraviesa el túnel. Nginx reenvía las cabeceras `Upgrade` y `Connection`. Sin token válido, el handshake se rechaza con 403.
- **SSE** `GET /events/stream?token=...`. Sigue disponible para uso local o con un túnel con nombre (`proxy_buffering off` en Nginx).

Cada notificación también se guarda en PostgreSQL para que sobreviva a recargas o reconexiones. El frontend se reconecta solo a los 3 s si el canal se cae.

| Evento | Disparador | Destinatario | Reacción |
|---|---|---|---|
| `remision_recibida` | Médico crea remisión | Especialista asignado | Bandeja + SSE |
| `remision_aceptada` | Especialista acepta | Médico remitente | Bandeja + SSE |
| `factura_automatica` | Clínico aprueba IA | Usuarios contables | Factura MongoDB + bandeja + SSE |
| `cuenta_bloqueada` | Tercer login fallido | Admin | Bandeja + SSE |
| `alerta_critica` | IA supera criterio crítico | Clínico que ejecutó | Alerta persistente + SSE |
| `alerta_escalada` | Alerta sigue activa pasado el tiempo | Admin | Bandeja + SSE + auditoría |
| `fhir_observation` | HAPI/gateway recibe nueva Observation (con Subscription rest-hook estándar) | Médico/especialista asignado | Bandeja + SSE + auditoría origen FHIR |

## Formato de los eventos (WebSocket y SSE)

```json
{
  "tipo": "remision_recibida",
  "mensaje": "Nueva remisión...",
  "fecha": "2026-10-14T20:00:00",
  "paciente_id": "uuid"
}
```

## Persistencia y privacidad

La tabla `notifications` guarda dueño, tipo, mensaje, `leida`, fecha y referencias del hecho. El endpoint solo entrega notificaciones del usuario autenticado. Un usuario no puede marcar como leída una notificación ajena.

## Alertas críticas y escalamiento

Una alerta se crea cuando el modelo supera `risk_score >= 0.70` o se cumple un criterio clínico de mal pronóstico (FEVI <= 25 % con creatinina >= 2.0 mg/dL, o sodio <= 128 mEq/L). La justificación clínica está en `docs/MODELO_ML.md`.

1. Se crea la alerta `activa`, asignada al clínico que ejecutó el análisis, y llega por SSE con `tipo = alerta_critica`.
2. El frontend la muestra como **aviso persistente** (banda roja fija arriba) hasta que el clínico la reconoce con una nota (`POST /alerts/{id}/acknowledge`). `paciente` y `contable` reciben 403.
3. Creación y reconocimiento quedan en la auditoría (`CRITICAL_ALERT_CREATED`, `CRITICAL_ALERT_ACKNOWLEDGED`).
4. **Escalamiento:** un proceso en segundo plano de la API revisa cada pocos segundos. Si la alerta sigue activa pasados `ALERT_ESCALATION_SECONDS` (120 s por defecto), la marca `escalated=true`, audita `CRITICAL_ALERT_ESCALATED` y notifica al admin (`alerta_escalada`).

## Flujo FHIR reactivo (R21)

Al iniciar, la API crea en HAPI una `Subscription` R4 con `criteria = Observation` y canal `rest-hook` hacia `http://api:8000/fhir/events/observation` (con una cabecera secreta `X-FHIR-Hook-Secret`). Cuando entra una Observation, HAPI hace el POST al hook; el reactor ubica al paciente por `subject.reference` (id FHIR, UUID local o documento), notifica a los clínicos asignados y audita `FHIR_EVENT_RECEIVED` con `origin = FHIR`.

El POST público `/fhir/Observation` pasa por un gateway de la API que la guarda en HAPI y dispara el mismo reactor de inmediato. Si después llega el rest-hook de HAPI con el mismo id, se descarta como duplicado. No hay sondeo periódico.

## Remisión → FHIR ServiceRequest

Al crear una remisión, en segundo plano se publica un `ServiceRequest` (SNOMED 3457005 *Patient referral*) en HAPI ligado al `Patient` FHIR. Si HAPI no responde, la remisión local no se afecta.
