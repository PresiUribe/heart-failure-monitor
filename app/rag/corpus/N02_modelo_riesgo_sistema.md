---
id: N02
tipo: norma
titulo: Uso y limitaciones del modelo de riesgo del sistema
fuente: Documentación interna del sistema Heart Failure Monitor (docs/MODELO_ML.md y docs/DATASETS.md), Salud Digital 2026-2.
---
Qué estima el modelo. El modelo de riesgo del sistema es una regresión logística entrenada con el dataset UCI Heart Failure Clinical Records. Estima un riesgo académico de evento adverso a partir de 12 variables: edad, anemia, CPK, diabetes, fracción de eyección, hipertensión, plaquetas, creatinina, sodio, sexo, tabaquismo y días de seguimiento. No es un diagnóstico ni un dispositivo médico validado; la decisión final es siempre del clínico.

Criterios de alerta crítica. Un caso es crítico si cumple al menos uno de estos criterios: probabilidad del modelo mayor o igual a 0,70; FEVI menor o igual a 25 % con creatinina mayor o igual a 2,0 mg/dL; o sodio menor o igual a 128 mEq/L. El reporte de IA queda pendiente hasta que un médico lo aprueba; solo los reportes aprobados entran a la historia clínica.

Limitaciones conocidas del modelo. El modelo se equivoca más en un grupo de pacientes más jóvenes (alrededor de 53 años) con FEVI baja y sodio bajo, donde tiende a subestimar el riesgo, y en pacientes con falla renal extrema. Su F1 es menor en menores de 65 años (0,824) que en mayores (0,952). Con grupos pequeños no puede afirmarse ni descartarse un sesgo; hace falta validación externa.
