"""Modelo base de risco de crédito (XGBoost) + limiar por custo + registro no MLflow."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.metrics import brier_score_loss, roc_auc_score, roc_curve
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier

XGB_PARAMS: dict[str, Any] = {
    "n_estimators": 450,
    "learning_rate": 0.05,
    "max_depth": 4,
    "min_child_weight": 5,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "tree_method": "hist",
    "eval_metric": "auc",
    "n_jobs": -1,
}


def to_model_frame(df: pd.DataFrame, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    """Tipos estáveis para o modelo (float64 / string) — mesmo contrato do Gate 2/3."""
    out = pd.DataFrame(index=df.index)
    for c in numeric:
        out[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in categorical:
        out[c] = df[c].astype(str)
    return out


def build_pipeline(numeric: list[str], categorical: list[str], seed: int) -> Pipeline:
    pre = ColumnTransformer(
        [("num", "passthrough", numeric), ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), categorical)],
        verbose_feature_names_out=True,
    )
    return Pipeline([("pre", pre), ("clf", XGBClassifier(random_state=seed, **XGB_PARAMS))])


def ks_statistic(y: np.ndarray, p: np.ndarray) -> float:
    fpr, tpr, _ = roc_curve(y, p)
    return float(np.max(tpr - fpr))


def expected_cost(y: np.ndarray, p: np.ndarray, credit: np.ndarray, t: float, lgd: float, margin: float) -> float:
    approve = p < t
    fn = approve & (y == 1)  # aprovou inadimplente
    fp = (~approve) & (y == 0)  # recusou bom pagador
    return float((credit[fn] * lgd).sum() + (credit[fp] * margin).sum())


def cost_optimal_threshold(y: np.ndarray, p: np.ndarray, credit: np.ndarray, lgd: float, margin: float) -> tuple[float, pd.DataFrame]:
    grid = np.round(np.arange(0.02, 0.61, 0.01), 2)
    costs = [expected_cost(y, p, credit, t, lgd, margin) for t in grid]
    curve = pd.DataFrame({"threshold": grid, "expected_cost": costs})
    return float(grid[int(np.argmin(costs))]), curve


def evaluate(y: np.ndarray, p: np.ndarray, credit: np.ndarray, t: float, lgd: float, margin: float) -> dict[str, float]:
    approve = p < t
    return {
        "auc": round(float(roc_auc_score(y, p)), 4),
        "ks": round(ks_statistic(y, p), 4),
        "brier": round(float(brier_score_loss(y, p)), 5),
        "default_rate": round(float(y.mean()), 4),
        "mean_pd": round(float(p.mean()), 4),
        "approval_rate": round(float(approve.mean()), 4),
        "recall_default": round(float(((~approve) & (y == 1)).sum() / max((y == 1).sum(), 1)), 4),
        "bad_rate_approved": round(float(y[approve].mean()) if approve.any() else 0.0, 4),
        "expected_loss": round(float((p[approve] * credit[approve] * lgd).sum()), 2),
        "realized_loss": round(float((y[approve] * credit[approve] * lgd).sum()), 2),
        "expected_cost": round(expected_cost(y, p, credit, t, lgd, margin), 2),
    }


def dataset_digest(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()


def training_domain(X: pd.DataFrame, numeric: list[str], categorical: list[str]) -> dict[str, Any]:
    """Domínio visto no treino — base do Gate 3 (training-serving skew)."""
    dom: dict[str, Any] = {"numeric": {}, "categorical": {}}
    for c in numeric:
        s = X[c].dropna()
        dom["numeric"][c] = {"p001": float(s.quantile(0.001)), "p999": float(s.quantile(0.999))}
    for c in categorical:
        dom["categorical"][c] = sorted(X[c].dropna().unique().tolist())
    return dom


def feature_importance(pipe: Pipeline, numeric: list[str], categorical: list[str]) -> dict[str, float]:
    names = pipe.named_steps["pre"].get_feature_names_out()
    gain = pipe.named_steps["clf"].feature_importances_
    agg: dict[str, float] = {f: 0.0 for f in numeric + categorical}
    for n, g in zip(names, gain):
        src = n.split("__", 1)[1]
        key = src if src in agg else next(c for c in categorical if src.startswith(c + "_"))
        agg[key] += float(g)
    total = sum(agg.values()) or 1.0
    return {k: round(v / total, 5) for k, v in sorted(agg.items(), key=lambda kv: -kv[1])}


def save_bundle(path: Path, pipe: Pipeline, meta: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, path / "model.joblib")
    (path / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def load_bundle(path: Path) -> tuple[Pipeline, dict[str, Any]]:
    return joblib.load(path / "model.joblib"), json.loads((path / "metadata.json").read_text(encoding="utf-8"))
