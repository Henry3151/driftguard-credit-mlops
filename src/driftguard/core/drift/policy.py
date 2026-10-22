"""Política de resposta em camadas (ADR-0006): resposta proporcional à severidade."""

from __future__ import annotations

from typing import Any

import pandas as pd

ACTIONS = {
    "estavel": "Monitoramento normal.",
    "leve": "Aumentar a frequência de monitoramento das features afetadas.",
    "moderado": "Recalibrar o limiar de decisão por custo esperado e investigar a causa raiz.",
    "severo": "Aplicar regra de negócio temporária no segmento afetado e disparar retreino (challenger com guardrails).",
}


def decide(
    drift_table: pd.DataFrame, perf: dict[str, float] | None, baseline: dict[str, float], slo: dict, mmd_significant: bool
) -> dict[str, Any]:
    important = drift_table[drift_table["important"]]
    severe_imp = important[important["severity"] == "severo"]["feature"].tolist()
    moderate_imp = important[important["severity"] == "moderado"]["feature"].tolist()
    any_drift = drift_table[drift_table["drift_detected"]]["feature"].tolist()
    auc_drop = (baseline["auc"] - perf["auc"]) if perf else 0.0
    brier_ratio = (perf["brier"] / baseline["brier"]) if perf else 1.0
    perf_breach = auc_drop > slo["model"]["auc_max_drop"]

    if perf_breach and (any_drift or mmd_significant):
        level = "severo"  # alerta composto: drift E degradação de desempenho
    elif severe_imp or perf_breach:
        level = "moderado"
    elif any_drift or mmd_significant:
        level = "leve" if not moderate_imp else "moderado"
    else:
        level = "estavel"
    return {
        "level": level,
        "action": ACTIONS[level],
        "auc_drop": round(auc_drop, 4),
        "brier_ratio": round(brier_ratio, 4),
        "performance_breach": perf_breach,
        "calibration_breach": brier_ratio > slo["model"]["brier_max_ratio"],
        "drifted_features": any_drift,
        "severe_important_features": severe_imp,
        "multivariate_drift": mmd_significant,
    }
