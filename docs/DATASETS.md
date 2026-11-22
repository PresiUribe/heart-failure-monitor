# Datasets del proyecto

## Imagen

**Nombre:** CAMUS — Cardiac Acquisitions for Multi-structure Ultrasound Segmentation.

**Fuente oficial:** https://www.creatis.insa-lyon.fr/Challenge/camus/databases.html

**Distribución de referencia para licencia/metadatos:** https://huggingface.co/datasets/zeahub/camus

**Licencia de la distribución usada:** CC BY-NC-SA 4.0, uso no comercial y científico. La descarga debe conservar los términos y la cita exigida por la fuente.

**Cohorte original:** 500 pacientes anonimizados con ecocardiografía 2D. Incluye secuencias apicales de 2 cámaras (2CH) y 4 cámaras (4CH), con fases de fin de diástole (ED) y fin de sístole (ES). La cohorte fue preparada para evaluación del ventrículo izquierdo y fracción de eyección.

**Número de imágenes cargadas:** 4. El script `import_camus_folder.py` actualiza este número al importar. Las imágenes reales no se redistribuyen en este repositorio. **Antes de la evaluación R06 se debe cargar al menos una imagen CAMUS real** con `scripts/import_camus_folder.py`. El número efectivo cargado se consulta en `GET /patients/{id}/images`.

**Uso en este proyecto:** la imagen real se almacena en Orthanc (PACS) y se relaciona con un paciente académico creado dentro de la aplicación. Esta asociación se registra con `academic_link=true`, `dataset=CAMUS` y el `source_subject_id` original. La asociación es docente y no afirma que los datos administrativos creados en la aplicación correspondan a la persona del estudio CAMUS.

**Pertinencia clínica:** CAMUS contiene ecocardiografías 2D orientadas a la evaluación estructural y funcional cardiaca, incluidas vistas utilizadas para estimar fracción de eyección del ventrículo izquierdo. Esto se alinea directamente con el seguimiento de insuficiencia cardiaca.

**Citación de CAMUS:** Leclerc S., Smistad E., Pedrosa J., Østvik A., et al. “Deep Learning for Segmentation using an Open Large-Scale Dataset in 2D Echocardiography”, IEEE Transactions on Medical Imaging, 2019. DOI: 10.1109/TMI.2019.2900516.

### Procedimiento de carga

1. Descargar CAMUS desde una fuente autorizada.
2. Mantener juntas las vistas/fases pertenecientes al mismo sujeto CAMUS.
3. Exportar a PNG/JPG los frames ED/ES cuando la distribución original esté en MHD/NIfTI, o usar DICOM cuando esté disponible.
4. Crear el paciente académico con `scripts/seed_demo_api.py` o desde la SPA.
5. Importar con `scripts/import_camus_folder.py`.
6. Verificar `GET /patients/{patient_id}/images` y `GET /images/{image_id}`.

---

## Tabular

**Nombre:** Heart Failure Clinical Records.

**Fuente:** https://archive.ics.uci.edu/dataset/519/heart%2Bfailure%2Bclinical%2Brecordst

**DOI:** https://doi.org/10.24432/C5Z89R

**Licencia:** Creative Commons Attribution 4.0 International (CC BY 4.0).

**Cohorte original:** 299 pacientes con insuficiencia cardiaca y 12 variables predictoras, más el desenlace `DEATH_EVENT`.

**Subconjunto real incluido localmente:** 187 registros reales de la cohorte UCI. El archivo es `data/uci_heart_failure_sample.csv`. Se utiliza un subconjunto para mantener el proyecto académico liviano; no se etiqueta ningún registro real como sintético.

**Variables principales:** edad, anemia, CPK, diabetes, fracción de eyección, hipertensión, plaquetas, creatinina sérica, sodio sérico, sexo, tabaquismo y tiempo de seguimiento.

**Pertinencia:** el dataset corresponde específicamente a pacientes con insuficiencia cardiaca y permite clasificación, regresión y clustering.

### Clustering y aumento sintético

El servicio `ml` estandariza cinco variables para clustering: edad, fracción de eyección (FEVI), creatinina sérica, sodio sérico y plaquetas. Se elige K-Means porque las variables son continuas y se busca un perfil medio interpretable por grupo.

#### Número de grupos

Se prueban `k = 2…8`. Se calculan la **silueta** (criterio principal) y la **inercia** (método del codo). Los valores se recalculan al iniciar el servicio y quedan en `GET /ml/metrics` → `clustering`; con `random_state=42` son reproducibles:

| k | Silueta | Inercia |
|---:|---:|---:|
| 2 | 0.178 | 773.2 |
| 3 | 0.178 | 667.1 |
| 4 | 0.184 | 564.4 |
| **5** | **0.187** | **502.2** |
| 6 | 0.183 | 453.6 |
| 7 | 0.171 | 421.7 |
| 8 | 0.169 | 388.8 |

Se escoge **k = 5**: es la silueta máxima, y a partir de k = 5 la caída de la inercia se aplana (codo). La silueta es baja en todos los k (~0.18), lo esperable en datos clínicos sin grupos naturalmente separados. Por eso los grupos se usan como **perfiles de riesgo** para el aumento y la interpretación, no como diagnósticos.

#### Interpretación clínica de los grupos

La tasa de error del modelo por grupo se calcula con predicciones **fuera de muestra** (validación cruzada estratificada de 5 particiones). Un grupo es *zona de confusión* si su error es >= 20 % y >= 1.25 veces el error global (15.5 %).

| Cluster | n reales / sintéticos | Perfil medio | Mortalidad | Error del modelo | Lectura clínica |
|---:|---:|---|---:|---:|---|
| 0 | 50 / 25 | 51 años, FEVI 38 %, creatinina 1.1, sodio 138, plaquetas altas | 28 % | 18 % | IC con FEVI reducida en pacientes jóvenes con riñón conservado: riesgo intermedio. |
| 1 | 35 / 17 | 53 años, FEVI 29 %, creatinina 1.6, sodio 131 | 54 % | **29 %** | **Zona de confusión.** FEVI muy reducida con hiponatremia y creatinina limítrofe en pacientes relativamente jóvenes. El modelo **subestima** el riesgo (7 falsos negativos frente a 3 falsos positivos): la edad joven "tranquiliza" al modelo aunque el perfil cardiorrenal sea de mal pronóstico. |
| 2 | 55 / 27 | 73 años, FEVI 34 %, creatinina 1.5, sodio 135 | 47 % | 7 % | Adulto mayor con FEVI reducida y creatinina limítrofe: riesgo alto que el modelo reconoce bien. |
| 3 | 43 / 21 | 63 años, FEVI 46 %, creatinina 1.1, sodio 140 | 21 % | 12 % | FEVI levemente reducida con función renal y sodio normales: el grupo de menor riesgo. |
| 4 | 4 / 5 | 63 años, FEVI 51 %, creatinina 7.8 | 100 % | **25 %** | **Zona de confusión.** Disfunción renal extrema con FEVI preservada. Un modelo lineal lo infravalora (falso negativo), lo que justifica los criterios clínicos complementarios del umbral (`MODELO_ML.md`). Grupo pequeño: hay que interpretarlo con cautela. |

`GET /clusters` devuelve por grupo `cluster`, `descripcion`, `n_reales`, `n_sinteticos`, `mortalidad_observada`, `tasa_error_modelo`, `zona_confusion`, falsos negativos/positivos y `perfil_medio`.

#### Aumento sintético

Después de asignar cada paciente real a un cluster, los sintéticos se generan **dentro de cada cluster** (n = max(5, n_cluster/2)). Se muestrea una normal con la media y la desviación del grupo, se recorta a los límites observados en el dataset real y se redondean las variables binarias o enteras. El desenlace se muestrea con la mortalidad del propio grupo. Cada fila conserva:

- `origin = real` o `origin = sintetico`;
- `cluster = <id>`.

El archivo aumentado queda en `/ml/artifacts/augmented_heart_failure.csv` dentro del contenedor ML.

#### Sobre el subconjunto de 187 registros

Se incluyen 187 de los 299 registros reales de UCI (todos reales, ninguno modificado) para mantener liviano el repositorio académico. Para usar la cohorte completa basta con reemplazar `data/uci_heart_failure_sample.csv` por el CSV original: el servicio recalcula modelo, métricas y clusters al iniciar, así que las tablas de arriba deberían actualizarse.

### Limitación académica

El modelo y los clusters son una demostración docente. No son un dispositivo médico ni han sido validados para decisiones clínicas reales.

## Corpus

Base documental del RAG (Semana 12) y del agente clínico (R23). Son resúmenes docentes, en español, de guías de práctica clínica y normas pertinentes a la insuficiencia cardiaca en seguimiento rural. Están en `app/rag/corpus/` y se indexan al arrancar.

| Id | Tipo | Título | Fuente |
|---|---|---|---|
| G01 | guia | Definición, clasificación y diagnóstico de la insuficiencia cardiaca | Resumen docente de McDonagh TA et al. 2021 ESC Guidelines for the diagnosis and treatment of acute and chronic heart failure. Eur Heart J. 2021;42:3599-3726. |
| G02 | guia | Tratamiento farmacológico de la insuficiencia cardiaca con FEVI reducida | Resumen docente de McDonagh TA et al. 2021 ESC Guidelines for the diagnosis and treatment of acute and chronic heart failure. Eur Heart J. 2021;42:3599-3726. |
| G03 | guia | Autocuidado, signos de alarma y descompensación | Resumen docente de Jaarsma T et al. Self-care of heart failure patients: practical management recommendations from the Heart Failure Association of the ESC. Eur J Heart Fail. 2021;23:157-174, y de las Guías ESC 2021 de insuficiencia cardiaca. |
| G04 | guia | Función renal, síndrome cardiorrenal y potasio | Resumen docente de las Guías ESC 2021 de insuficiencia cardiaca (McDonagh TA et al., Eur Heart J. 2021;42:3599-3726) y de Chicco D, Jurman G. Machine learning can predict survival of patients with heart failure from serum creatinine and ejection fraction alone. BMC Med Inform Decis Mak. 2020;20:16. |
| G05 | guia | Hiponatremia en insuficiencia cardiaca | Resumen docente de Gheorghiade M et al. Relationship between admission serum sodium concentration and clinical outcomes in patients hospitalized for heart failure: an analysis from the OPTIMIZE-HF registry. Eur Heart J. 2007;28:980-988, y de las Guías ESC 2021 de insuficiencia cardiaca. |
| G06 | guia | Ecocardiografía y medición de la fracción de eyección | Resumen docente de Lang RM et al. Recommendations for cardiac chamber quantification by echocardiography in adults: an update from the ASE and the EACVI. J Am Soc Echocardiogr. 2015;28:1-39, y de Leclerc S et al. Deep learning for segmentation using an open large-scale dataset in 2D echocardiography (CAMUS). IEEE Trans Med Imaging. 2019;38:2198-2210. |
| G07 | guia | Seguimiento, telemonitoreo y remisión al especialista | Resumen docente de McDonagh TA et al. 2021 ESC Guidelines for the diagnosis and treatment of acute and chronic heart failure. Eur Heart J. 2021;42:3599-3726. |
| N01 | norma | Normativa colombiana sobre datos de salud e historia clínica | Resumen docente de la Ley 1581 de 2012 (protección de datos personales), la Ley 2015 de 2020 (historia clínica electrónica interoperable), la Resolución 866 de 2021 del Ministerio de Salud y Protección Social (elementos de datos clínicos para la interoperabilidad) y la Resolución 1995 de 1999 (manejo de la historia clínica). |
| N02 | norma | Uso y limitaciones del modelo de riesgo del sistema | Documentación interna del sistema Heart Failure Monitor (docs/MODELO_ML.md y docs/DATASETS.md), Salud Digital 2026-2. |

Además, por cada paciente se indexa su **historia aprobada**: ficha, observaciones, consultas y reportes de IA aprobados. Esa historia se seudonimiza antes de indexarse y solo la puede recuperar una pregunta hecha sobre ese mismo paciente. Los reportes pendientes o rechazados no entran (R10).

Cómo se citan: cada respuesta de `POST /agente/chat` y de `POST /rag/preguntar` trae la lista `fuentes`, con documento, fragmento y número `[n]`. Si el RAG no supera los umbrales de similitud (coseno 0,35; BM25 4,0), el sistema se abstiene y dice que no hay evidencia (`docs/RAG.md`).
