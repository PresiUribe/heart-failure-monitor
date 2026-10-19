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
