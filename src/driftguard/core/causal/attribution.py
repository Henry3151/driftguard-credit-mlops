"""Análise causal da degradação do modelo.

1. Decomposição covariate × concept: avalia a MESMA janela com o rótulo "do conceito antigo"
   (contrafactual: como os clientes teriam se comportado sem a mudança de risco) e com o
   rótulo real. A diferença isola o efeito do concept drift; o restante vem da mudança de
   população (covariate shift).
2. Ablação contrafactual por feature: cada feature com drift é devolvida à distribuição da
   referência por *quantile mapping* (preserva a ordem dos clientes). O quanto a métrica
   volta indica a contribuição daquela feature para a mudança observada.
3. Validação: compara o que foi detectado com o que foi injetado pelo simulador (gabarito).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from driftguard.core.data.canonical import add_derived_features
from driftguard.core.model.train import to_model_frame

BASE_DRIVERS = ["income", "annuity", "credit_amount", "age_years"]


def quantile_map(current: pd.Series, reference: pd.Series) -> pd.Series:
    ranks = current.rank(pct=True, method="average")
    mapped = np.quantile(reference.dropna(), ranks.fillna(0.5).clip(0, 1))
    return pd.Series(np.where(current.isna(), np.nan, mapped), index=current.index)


def _score(pipe, df: pd.DataFrame, numeric: list[str], categorical: list[str]) -> np.ndarray:
    return pipe.predict_proba(to_model_frame(add_derived_features(df), numeric, categorical))[:, 1]


def decompose(
    pipe, window: pd.DataFrame, gt: pd.DataFrame, numeric: list[str], categorical: list[str], baseline_auc: float, baseline_default: float
) -> dict[str, Any]:
    p = _score(pipe, window, numeric, categorical)
    y_real = window["target"].astype(int).to_numpy()
    y_orig = gt["target_original"].astype(int).to_numpy()
    auc_real, auc_orig = roc_auc_score(y_real, p), roc_auc_score(y_orig, p)
    return {
        "auc_baseline": round(baseline_auc, 4),
        "auc_contrafactual_sem_concept": round(float(auc_orig), 4),
        "auc_real": round(float(auc_real), 4),
        "efeito_covariate_shift_auc": round(float(auc_orig - baseline_auc), 4),
        "efeito_concept_drift_auc": round(float(auc_real - auc_orig), 4),
        "default_rate_baseline": round(baseline_default, 4),
        "default_rate_mudanca_populacao": round(float(y_orig.mean()), 4),
        "default_rate_real": round(float(y_real.mean()), 4),
        "pd_media_prevista": round(float(p.mean()), 4),
        "subestimacao_risco_pp": round(float(y_real.mean() - p.mean()) * 100, 2),
    }


def feature_ablation(
    pipe,
    window: pd.DataFrame,
    reference: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
    threshold: float,
    drivers: list[str] | None = None,
) -> pd.DataFrame:
    drivers = drivers or BASE_DRIVERS
    p_cur = _score(pipe, window, numeric, categorical)
    base = {"pd_media": p_cur.mean(), "aprovacao": (p_cur < threshold).mean()}
    p_ref = _score(pipe, reference, numeric, categorical)
    ref_stats = {"pd_media": p_ref.mean(), "aprovacao": (p_ref < threshold).mean()}
    total_pd = base["pd_media"] - ref_stats["pd_media"]
    total_ap = base["aprovacao"] - ref_stats["aprovacao"]
    rows = []
    for f in drivers:
        abl = window.copy()
        abl[f] = quantile_map(pd.to_numeric(window[f], errors="coerce"), pd.to_numeric(reference[f], errors="coerce"))
        p_abl = _score(pipe, abl, numeric, categorical)
        d_pd = base["pd_media"] - p_abl.mean()
        d_ap = base["aprovacao"] - (p_abl < threshold).mean()
        rows.append(
            {
                "feature": f,
                "delta_pd_media_pp": round(float(d_pd) * 100, 3),
                "share_mudanca_pd": round(float(d_pd / total_pd), 3) if abs(total_pd) > 1e-9 else np.nan,
                "delta_aprovacao_pp": round(float(d_ap) * 100, 3),
                "share_mudanca_aprovacao": round(float(d_ap / total_ap), 3) if abs(total_ap) > 1e-9 else np.nan,
            }
        )
    out = pd.DataFrame(rows).sort_values("delta_pd_media_pp", key=np.abs, ascending=False).reset_index(drop=True)
    out.attrs["total_delta_pd_pp"] = round(float(total_pd) * 100, 3)
    out.attrs["total_delta_aprovacao_pp"] = round(float(total_ap) * 100, 3)
    return out


def segment_effect(pipe, window: pd.DataFrame, gt: pd.DataFrame, numeric: list[str], categorical: list[str]) -> dict[str, Any]:
    p = _score(pipe, window, numeric, categorical)
    seg = gt["in_segment"].to_numpy()
    y = window["target"].astype(int).to_numpy()
    y0 = gt["target_original"].astype(int).to_numpy()
    out = {}
    for name, mask in (("segmento_vulneravel", seg), ("demais_clientes", ~seg)):
        out[name] = {
            "n": int(mask.sum()),
            "default_real": round(float(y[mask].mean()), 4),
            "default_sem_concept": round(float(y0[mask].mean()), 4),
            "pd_prevista": round(float(p[mask].mean()), 4),
        }
    return out
