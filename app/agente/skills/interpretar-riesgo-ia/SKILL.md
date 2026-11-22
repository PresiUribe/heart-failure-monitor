---
name: interpretar-riesgo-ia
description: Cómo leer el reporte del modelo de riesgo (IA) de un paciente, sus criterios críticos y sus limitaciones, y qué reportar al clínico.
---
# Interpretación del reporte de riesgo de IA

1. Llama a `consultar_reportes_ia`. Usa solo reportes APROBADOS; si hay pendientes, recuérdale al médico que debe revisarlos en el sistema.
2. Reporta el riesgo (porcentaje), el nivel (bajo, alto o crítico) y cuáles criterios críticos se cumplieron: probabilidad mayor o igual a 0,70, FEVI menor o igual a 25 % con creatinina mayor o igual a 2,0 mg/dL, o sodio menor o igual a 128 mEq/L.
3. Si la pregunta lo pide, llama a `buscar_guias` para el significado clínico del hallazgo (por ejemplo "hiponatremia pronóstico" o "síndrome cardiorrenal creatinina").
4. Advierte las limitaciones conocidas del modelo (guía N02): es un riesgo académico, no un diagnóstico; se equivoca más en pacientes más jóvenes con FEVI y sodio bajos, donde subestima el riesgo, y en falla renal extrema.
5. Si hay una alerta crítica activa, dilo primero: debe reconocerla un clínico con nota en el sistema.
6. No propongas medicamentos ni dosis. Cierra con "Sugerencia para revisión del clínico".
