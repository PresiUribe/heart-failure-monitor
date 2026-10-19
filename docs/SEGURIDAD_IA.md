# Seguridad de datos hacia IA

La API no envía identidad civil al servicio ML. Antes de ejecutar el análisis genera un identificador seudónimo estable mediante SHA-256/HMAC-like usando el secreto del despliegue.

El payload permitido contiene únicamente:

- `pseudonym`;
- variables clínicas UCI necesarias para el modelo;
- opcionalmente `image_id` como referencia técnica, nunca el nombre del paciente.

No se envían nombre, documento, correo, teléfono ni dirección. El JSON exacto enviado se guarda en `ml_payloads` y se consulta con `GET /ai/reports/{report_id}/payload`.

Ejemplo:

```json
{
  "pseudonym": "PSN-...",
  "age": 72,
  "ejection_fraction": 20,
  "serum_creatinine": 2.3,
  "serum_sodium": 126,
  "image_id": null
}
```

La seudonimización no convierte los datos clínicos en datos anónimos. El proyecto la usa como control de minimización y circulación restringida entre servicios.
