from pathlib import Path
import json
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sklearn.cluster import KMeans
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, recall_score, precision_score, roc_auc_score, silhouette_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import os
DATA=Path(os.getenv("ML_DATA_PATH","/data/uci_heart_failure_sample.csv"))
THRESHOLD=float(os.getenv("CRITICAL_RISK_THRESHOLD","0.70"))
ART=Path(os.getenv("ML_ARTIFACTS_PATH","/ml/artifacts"))
FEATURES=["age","anaemia","creatinine_phosphokinase","diabetes","ejection_fraction","high_blood_pressure","platelets","serum_creatinine","serum_sodium","sex","smoking","time"]
BINARY={"anaemia","diabetes","high_blood_pressure","sex","smoking"}
app=FastAPI(title="Heart Failure ML Service",version="2.0.0")
model=None; cluster_model=None; cluster_scaler=None; cluster_summary=[]; metrics={}

class Input(BaseModel):
    pseudonym: str
    age: float
    anaemia: int
    creatinine_phosphokinase: float
    diabetes: int
    ejection_fraction: float
    high_blood_pressure: int
    platelets: float
    serum_creatinine: float
    serum_sodium: float
    sex: int
    smoking: int
    time: int=30
    image_id: str|None=None


def describe_cluster(stats, overall):
    """Interpretación clínica de un grupo a partir de sus medias y del error del modelo.

    stats: medias del grupo + mortalidad + tasa de error/ambigüedad del modelo
    (predicciones fuera de muestra, validación cruzada de 5 particiones).
    """
    ef, cr, na, age = stats["ejection_fraction"], stats["serum_creatinine"], stats["serum_sodium"], stats["age"]
    rasgos = []
    if ef < 30: rasgos.append(f"FEVI muy reducida ({ef:.0f}%)")
    elif ef < 40: rasgos.append(f"FEVI reducida ({ef:.0f}%)")
    elif ef < 50: rasgos.append(f"FEVI levemente reducida ({ef:.0f}%)")
    else: rasgos.append(f"FEVI preservada ({ef:.0f}%)")
    if cr >= 1.8: rasgos.append(f"disfunción renal marcada (creatinina {cr:.1f} mg/dL)")
    elif cr >= 1.3: rasgos.append(f"creatinina limítrofe-alta ({cr:.1f} mg/dL)")
    else: rasgos.append(f"función renal conservada (creatinina {cr:.1f} mg/dL)")
    if na < 135: rasgos.append(f"hiponatremia ({na:.0f} mEq/L)")
    if age >= 70: rasgos.append(f"edad avanzada ({age:.0f} años)")
    elif age < 58: rasgos.append(f"pacientes más jóvenes ({age:.0f} años)")
    if stats["platelets"] > 330000: rasgos.append("plaquetas altas")
    mort = stats["mortalidad"]
    if mort >= overall["mortalidad"] * 1.4: riesgo = "riesgo alto"
    elif mort <= overall["mortalidad"] * 0.7: riesgo = "riesgo bajo"
    else: riesgo = "riesgo intermedio"
    texto = f"{riesgo.capitalize()}: " + ", ".join(rasgos) + f". Mortalidad observada {mort:.0%}."
    if stats["zona_confusion"]:
        if stats["falsos_negativos"] > stats["falsos_positivos"]:
            sesgo = "subestima el riesgo (predominan falsos negativos: fallecidos clasificados como sobrevivientes)"
        elif stats["falsos_positivos"] > stats["falsos_negativos"]:
            sesgo = "sobreestima el riesgo (predominan falsos positivos: sobrevivientes clasificados como fallecidos)"
        else:
            sesgo = "confunde por igual sobrevivientes y fallecidos"
        texto += (f" ZONA DE CONFUSIÓN: el modelo se equivoca en {stats['tasa_error']:.0%} de los casos "
                  f"(promedio global {overall['tasa_error']:.0%}) y {sesgo}; aquí la decisión exige juicio clínico.")
    else:
        texto += f" El modelo discrimina bien este grupo (error {stats['tasa_error']:.0%})."
    return texto


def build():
    global model,cluster_model,cluster_scaler,cluster_summary,metrics
    df=pd.read_csv(DATA)
    X=df[FEATURES].astype(float); y=df["DEATH_EVENT"].astype(int)
    Xtr,Xte,ytr,yte=train_test_split(X,y,test_size=.25,random_state=42,stratify=y)
    model=Pipeline([("scale",StandardScaler()),("clf",LogisticRegression(max_iter=2000,class_weight="balanced",random_state=42))])
    model.fit(Xtr,ytr)
    prob=model.predict_proba(Xte)[:,1]; pred=(prob>=.5).astype(int)
    metrics={"dataset":"UCI Heart Failure Clinical Records (subconjunto real incluido)","n_real":int(len(df)),"accuracy":float(accuracy_score(yte,pred)),"precision":float(precision_score(yte,pred,zero_division=0)),"recall":float(recall_score(yte,pred,zero_division=0)),"f1":float(f1_score(yte,pred,zero_division=0)),"roc_auc":float(roc_auc_score(yte,prob)),"threshold_critical":THRESHOLD}

    # Análisis académico de desempeño por subgrupo (R22). No demuestra
    # ausencia de sesgo; ayuda a identificar diferencias que deben discutirse.
    test_view = df.loc[Xte.index, ["sex", "age"]].copy()
    test_view["y"] = yte
    test_view["pred"] = pred
    test_view["prob"] = prob
    subgroup_metrics = {}
    for label, mask in {
        "sex_0": test_view["sex"] == 0,
        "sex_1": test_view["sex"] == 1,
        "age_lt65": test_view["age"] < 65,
        "age_ge65": test_view["age"] >= 65,
    }.items():
        g = test_view.loc[mask]
        if len(g) == 0:
            continue
        auc = None
        if g["y"].nunique() > 1:
            auc = float(roc_auc_score(g["y"], g["prob"]))
        subgroup_metrics[label] = {
            "n": int(len(g)),
            "positive_rate": float(g["y"].mean()),
            "recall": float(recall_score(g["y"], g["pred"], zero_division=0)),
            "precision": float(precision_score(g["y"], g["pred"], zero_division=0)),
            "f1": float(f1_score(g["y"], g["pred"], zero_division=0)),
            "roc_auc": auc,
        }
    metrics["subgroups"] = subgroup_metrics

    # Probabilidades fuera de muestra para todo el dataset real: permiten saber
    # en qué clusters el modelo se confunde sin evaluarlo sobre datos de entrenamiento.
    oof_prob = cross_val_predict(
        Pipeline([("scale",StandardScaler()),("clf",LogisticRegression(max_iter=2000,class_weight="balanced",random_state=42))]),
        X, y, cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=42), method="predict_proba",
    )[:, 1]
    df["_oof_prob"] = oof_prob
    df["_oof_error"] = ((oof_prob >= .5).astype(int) != y.values).astype(int)

    cluster_features=["age","ejection_fraction","serum_creatinine","serum_sodium","platelets"]
    CX=df[cluster_features].astype(float)
    cluster_scaler=StandardScaler().fit(CX)
    Z=cluster_scaler.transform(CX)
    scores={}; inertias={}
    for k in range(2,min(9,len(df)-1)):
        km=KMeans(n_clusters=k,random_state=42,n_init=20).fit(Z)
        scores[k]=silhouette_score(Z,km.labels_); inertias[k]=float(km.inertia_)
    best_k=max(scores,key=scores.get)
    metrics["clustering"] = {
        "criterion": "silhouette",
        "best_k": int(best_k),
        "silhouette_scores": {str(k): float(v) for k, v in scores.items()},
        "inertia_elbow": {str(k): v for k, v in inertias.items()},
    }
    cluster_model=KMeans(n_clusters=best_k,random_state=42,n_init=30).fit(Z)
    df["cluster"]=cluster_model.labels_
    augmented=[]; rng=np.random.default_rng(42)
    for c in sorted(df.cluster.unique()):
        grp=df[df.cluster==c].copy(); center=grp[cluster_features].mean().values
        n_syn=max(5,len(grp)//2)
        for _ in range(n_syn):
            row={}
            for col in FEATURES:
                vals=grp[col].astype(float); v=float(rng.normal(vals.mean(), max(vals.std(ddof=0),1e-6)))
                v=float(np.clip(v,df[col].astype(float).min(),df[col].astype(float).max()))
                if col in BINARY: v=int(v>=.5)
                if col in {"age","ejection_fraction","serum_sodium","time"}: v=round(v)
                row[col]=v
            row["DEATH_EVENT"]=int(rng.random()<grp.DEATH_EVENT.mean()); row["origin"]="sintetico"; row["cluster"]=int(c); augmented.append(row)
    real=df.drop(columns=["_oof_prob","_oof_error"]).copy(); real["origin"]="real"
    aug=pd.concat([real,pd.DataFrame(augmented)],ignore_index=True)
    ART.mkdir(parents=True,exist_ok=True); aug.to_csv(ART/"augmented_heart_failure.csv",index=False)
    cluster_summary=[]
    overall={"mortalidad":float(df.DEATH_EVENT.mean()),"tasa_error":float(df["_oof_error"].mean())}
    for c in sorted(real.cluster.unique()):
        g=df[df.cluster==c]
        n_real=int(len(g)); n_syn=int(sum(1 for x in augmented if x["cluster"]==int(c)))
        stats={k:float(g[k].mean()) for k in ["age","ejection_fraction","serum_creatinine","serum_sodium","platelets"]}
        stats["mortalidad"]=float(g.DEATH_EVENT.mean())
        stats["tasa_error"]=float(g["_oof_error"].mean())
        stats["ambiguedad"]=float(((g["_oof_prob"]>=.35)&(g["_oof_prob"]<=.65)).mean())
        stats["zona_confusion"]=bool(stats["tasa_error"]>=max(0.20, overall["tasa_error"]*1.25))
        errs=g[g["_oof_error"]==1]
        stats["falsos_negativos"]=int((errs.DEATH_EVENT==1).sum()); stats["falsos_positivos"]=int((errs.DEATH_EVENT==0).sum())
        cluster_summary.append({
            "cluster":int(c),
            "descripcion":describe_cluster(stats, overall),
            "n_reales":n_real,
            "n_sinteticos":n_syn,
            "mortalidad_observada":round(stats["mortalidad"],3),
            "tasa_error_modelo":round(stats["tasa_error"],3),
            "zona_confusion":stats["zona_confusion"],
            "falsos_negativos":stats["falsos_negativos"],
            "falsos_positivos":stats["falsos_positivos"],
            "perfil_medio":{k:round(v,2) for k,v in stats.items() if k in {"age","ejection_fraction","serum_creatinine","serum_sodium","platelets"}},
        })
    metrics["clustering"]["overall_error_rate"]=overall["tasa_error"]
    (ART/"metrics.json").write_text(json.dumps(metrics,indent=2),encoding="utf-8")
    (ART/"cluster_summary.json").write_text(json.dumps(cluster_summary,indent=2,ensure_ascii=False),encoding="utf-8")

@app.on_event("startup")
def startup(): build()

@app.get("/health")
def health(): return {"status":"ok","model_ready":model is not None}

@app.get("/clusters")
def clusters(): return cluster_summary

@app.get("/metrics")
def get_metrics(): return metrics

@app.post("/predict")
def predict(p: Input):
    if model is None: raise HTTPException(503,"Modelo no disponible")
    row=pd.DataFrame([{k:getattr(p,k) for k in FEATURES}])
    risk=float(model.predict_proba(row)[0,1])
    # Criterio crítico = probabilidad del modelo >= umbral  O  regla clínica de
    # marcadores de mal pronóstico (ver docs/MODELO_ML.md, sección "Umbral"):
    #  - FEVI <= 25 % con creatinina >= 2.0 mg/dL (falla sistólica severa + disfunción renal)
    #  - sodio <= 128 mEq/L (hiponatremia moderada-severa)
    criterios=[]
    if risk>=THRESHOLD: criterios.append(f"probabilidad del modelo >= {THRESHOLD:.2f}")
    if p.ejection_fraction<=25 and p.serum_creatinine>=2.0: criterios.append("FEVI <= 25% con creatinina >= 2.0 mg/dL")
    if p.serum_sodium<=128: criterios.append("sodio <= 128 mEq/L")
    level="CRITICAL" if criterios else ("HIGH" if risk>=.50 else "LOW")
    return {"model":"uci-heart-failure-logistic-v1","risk_score":risk,"prediction":int(risk>=.5),"level":level,"criterios_criticos":criterios,"pseudonym":p.pseudonym}
