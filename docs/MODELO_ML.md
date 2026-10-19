# Modelo supervisado, umbral y análisis de subgrupos

## Propósito académico

El servicio `ml` demuestra aprendizaje supervisado aplicado al problema de insuficiencia cardiaca. La base teórica es **Heart Failure Clinical Records (UCI)** y el desenlace supervisado es `DEATH_EVENT` de la cohorte original.

El resultado se presenta en la aplicación como **riesgo académico de evento adverso** y nunca como diagnóstico. El modelo no está validado como dispositivo médico y no debe utilizarse para decisiones clínicas reales.

## Datos y variables

El proyecto incluye 187 registros reales del dataset UCI en `data/uci_heart_failure_sample.csv`.

Variables utilizadas:

- edad;
- anemia;
- creatina fosfoquinasa (CPK);
- diabetes;
- fracción de eyección;
- hipertensión;
- plaquetas;
- creatinina sérica;
- sodio sérico;
- sexo;
- tabaquismo;
- tiempo de seguimiento.

La separación de entrenamiento/prueba es estratificada, con `random_state=42` y 25 % para prueba.

## Modelo supervisado

Se utiliza una **regresión logística** dentro de un `Pipeline` con:

1. `StandardScaler` para estandarizar variables;
2. `LogisticRegression(max_iter=2000, class_weight="balanced")`.

La elección de regresión logística favorece una salida probabilística e interpretación sencilla para una demostración docente.

## Métricas de validación local

Con la versión incluida del subconjunto real y la semilla fija se obtuvieron:

| Métrica | Resultado |
|---|---:|
| Accuracy | 0.915 |
| Precision | 0.850 |
| Recall | 0.944 |
| F1 | 0.895 |
| ROC-AUC | 0.954 |

Estas cifras describen solamente la partición académica incluida en el proyecto. **No son evidencia de validez clínica externa.**

El endpoint `GET /ml/metrics` entrega estas métricas desde el servicio en ejecución.

## Umbral de alerta crítica

Un caso se clasifica como `CRITICAL` (y dispara la alerta de R20) cuando se cumple **al menos uno** de estos criterios. El servicio ML devuelve en `criterios_criticos` cuál(es) se cumplieron, para que el clínico vea por qué se generó la alerta.

| Criterio | Valor | Justificación clínica |
|---|---|---|
| Probabilidad del modelo | `risk_score >= 0.70` | Con este punto de corte el modelo opera en zona de alta especificidad: se reserva la alerta para casos en que el riesgo de muerte en el seguimiento es claramente mayor que la mortalidad base de la cohorte (~32 %). Un umbral de 0.5 generaría demasiadas alertas (fatiga de alarmas). Configurable con `CRITICAL_RISK_THRESHOLD`. |
| Falla sistólica severa + disfunción renal | FEVI `<= 25 %` **y** creatinina `>= 2.0 mg/dL` | FEVI < 40 % define IC con fracción reducida (guías ESC 2021); valores <= 25–30 % indican disfunción sistólica severa. La creatinina elevada refleja síndrome cardiorrenal. En esta misma cohorte, FEVI y creatinina sérica son las dos variables con mayor poder predictivo de mortalidad (Chicco & Jurman, 2020). |
| Hiponatremia moderada–severa | sodio `<= 128 mEq/L` | La hiponatremia es un marcador independiente de mortalidad en IC descompensada (registro OPTIMIZE-HF; Gheorghiade et al., 2007). Valores <= 128 mEq/L se consideran moderados a severos. |

Los criterios clínicos complementan al modelo porque un modelo lineal puede subestimar casos extremos (ver el cluster de creatinina muy alta en `DATASETS.md`, donde el modelo produce falsos negativos). Los valores de `paciente_critico` en `tester.yaml` (FEVI 20 %, creatinina 2.3, sodio 126) cumplen los tres criterios.

**Escalamiento:** si la alerta sigue activa pasados `ALERT_ESCALATION_SECONDS` (120 s por defecto), se notifica al admin (`alerta_escalada`) y se audita `CRITICAL_ALERT_ESCALATED`.

Referencias: McDonagh T. et al. *2021 ESC Guidelines for heart failure*, Eur Heart J 2021;42:3599–3726. Chicco D., Jurman G. *Machine learning can predict survival of patients with heart failure from serum creatinine and ejection fraction alone*, BMC Med Inform Decis Mak 2020;20:16. Gheorghiade M. et al. *Characterization and prognostic value of persistent hyponatremia in patients with severe heart failure*, Eur Heart J 2007;28:980–988.

> Proyecto académico: los umbrales están justificados con literatura, pero no han sido validados para uso clínico real.

## Análisis de desempeño por subgrupo

Se calculan métricas separadas por sexo codificado en el dataset UCI y por edad (<65 / >=65 años).

| Subgrupo | n test | Recall | Precision | F1 | ROC-AUC |
|---|---:|---:|---:|---:|---:|
| sex = 0 | 22 | 1.000 | 0.800 | 0.889 | 0.946 |
| sex = 1 | 25 | 0.900 | 0.900 | 0.900 | 0.973 |
| edad < 65 | 28 | 0.875 | 0.778 | 0.824 | 0.963 |
| edad >= 65 | 19 | 1.000 | 0.909 | 0.952 | 0.922 |

### Interpretación

Se observan diferencias de desempeño entre subgrupos, especialmente en precision/F1 del grupo de edad menor a 65 años. Debido al tamaño reducido de la muestra, estas diferencias **no permiten concluir presencia o ausencia de sesgo clínico**. Se documentan porque R22 exige analizar el comportamiento del modelo por subgrupos y porque en un desarrollo real se requeriría una cohorte mucho mayor y validación externa.

## Clustering y aumento sintético

Para R07 se aplica K-Means sobre:

- edad;
- fracción de eyección;
- creatinina sérica;
- sodio sérico;
- plaquetas.

Las variables se estandarizan y se prueban `k=2..5`. Se selecciona el mayor **silhouette score**.

| k | Silhouette |
|---:|---:|
| 2 | 0.1782 |
| 3 | 0.1778 |
| 4 | 0.1837 |
| 5 | **0.1868** |

Por tanto, en la versión reproducible incluida se seleccionan **5 clusters**.

| Cluster | Interpretación académica | Reales | Sintéticos |
|---:|---|---:|---:|
| 0 | Fracción de eyección reducida con función renal relativamente conservada | 50 | 25 |
| 1 | Fracción de eyección reducida y creatinina elevada | 35 | 17 |
| 2 | Fracción de eyección reducida y creatinina elevada | 55 | 27 |
| 3 | Menor severidad relativa dentro de la cohorte | 43 | 21 |
| 4 | Menor severidad relativa dentro de la cohorte | 4 | 5 |

Los registros sintéticos se generan **dentro de cada cluster**, se marcan con `origin=sintetico` y conservan su `cluster`. Los registros originales se marcan `origin=real`.

## Seguridad y privacidad de entrada al modelo

El microservicio ML no recibe nombre, documento, correo, teléfono ni dirección. La API genera un seudónimo y envía únicamente variables clínicas necesarias más un `image_id` opcional. El payload exacto se guarda en `ml_payloads` para poder demostrar R12.

La imagen CAMUS se integra al flujo clínico/PACS mediante su identificador; esta versión **no afirma que el clasificador tabular analice los píxeles de la ecocardiografía**. R06 y el modelo tabular se mantienen técnicamente separados para no introducir una capacidad no implementada.
