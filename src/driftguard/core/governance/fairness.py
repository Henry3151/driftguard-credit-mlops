"""Auditoria de vieses (LGPD art. 6º, IX — não discriminação).

Atributos sensíveis vêm da tabela de auditoria separada (ADR-0008) e NUNCA do modelo.
Métricas por grupo: taxa de aprovação, disparate impact (regra dos 4/5),
equal opportunity (aprovação entre bons pagadores) e AUC por grupo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


def group_metrics(y: np.ndarray, p: np.ndarray, approve: np.ndarray, groups: pd.Series, min_size: int) -> pd.DataFrame:
    rows = []
    for g, idx in groups.groupby(groups).groups.items():
        mask = groups.index.isin(idx)
        n = int(mask.sum())
        if n < min_size:  # supressão de grupos pequenos (risco de reidentificação)
            continue
        yy, pp, aa = y[mask], p[mask], approve[mask]
        good = yy == 0
        rows.append(
            {
                "group": str(g),
                "n": n,
                "approval_rate": float(aa.mean()),
                "good_payer_approval_rate": float(aa[good].mean()) if good.any() else np.nan,
                "default_rate": float(yy.mean()),
                "auc": float(roc_auc_score(yy, pp)) if 0 < yy.sum() < n else np.nan,
            }
        )
    return pd.DataFrame(rows)


def fairness_summary(y: np.ndarray, p: np.ndarray, threshold: float, audit: pd.DataFrame, min_size: int) -> pd.DataFrame:
    approve = p < threshold
    out = []
    for attr in audit.columns:
        gm = group_metrics(y, p, approve, audit[attr].reset_index(drop=True), min_size)
        if len(gm) < 2:
            continue
        di = gm["approval_rate"].min() / gm["approval_rate"].max()
        eo = gm["good_payer_approval_rate"].max() - gm["good_payer_approval_rate"].min()
        auc_gap = gm["auc"].max() - gm["auc"].min()
        worst = gm.loc[gm["approval_rate"].idxmin(), "group"]
        out.append(
            {
                "attribute": attr,
                "groups": len(gm),
                "disparate_impact": round(float(di), 4),
                "equal_opportunity_gap": round(float(eo), 4),
                "auc_gap": round(float(auc_gap), 4),
                "least_approved_group": worst,
                "detail": gm.round(4).to_dict(orient="records"),
            }
        )
    return pd.DataFrame(out)
