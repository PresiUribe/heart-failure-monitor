---
name: remision-cardiologia
description: Procedimiento para decidir y proponer la remisión de un paciente con insuficiencia cardiaca a cardiología o a una unidad de falla cardiaca.
---
# Remisión a cardiología

1. Llama a `consultar_paciente` para tener los datos y observaciones actuales.
2. Llama a `consultar_reportes_ia`: anota riesgo, nivel, criterios críticos, FEVI, creatinina y sodio del último reporte aprobado.
3. Llama a `buscar_guias` con el hallazgo concreto, por ejemplo "remisión a cardiología FEVI menor o igual a 35 %" o "criterios de remisión insuficiencia cardiaca avanzada".
4. Remite solo si una fuente lo respalda. Criterios de la guía G07: síntomas NYHA III o IV pese a tratamiento óptimo, hospitalizaciones repetidas, hipotensión o deterioro renal que impide titular fármacos, o FEVI menor o igual a 35 % tras 3 meses de tratamiento óptimo.
5. Escribe el motivo así: "<hallazgo del paciente> + <criterio de la guía [n]>". Ejemplo: "FEVI 20 % y creatinina 2,3 mg/dL en reporte aprobado; la guía [1] indica remitir con FEVI menor o igual a 35 %".
6. Marca como PRIORITARIA en el motivo si hay alerta crítica activa, sodio menor o igual a 128 mEq/L o FEVI menor o igual a 25 % con creatinina mayor o igual a 2,0.
7. Llama SIEMPRE a `proponer_remision` con especialidad "cardiologia" y ese motivo cuando el médico pidió remitir y hay un criterio que la respalde. No basta con decir en texto que cumple criterios: sin esta llamada no existe la remisión. El sistema la detiene hasta que el médico la apruebe o la rechace.
8. Si el médico rechaza, no insistas: resume por qué lo propusiste y pregunta qué conducta prefiere.
