"""Relatórios oficiais de drift com Evidently AI (relatório de referência para auditoria)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
from evidently import BinaryClassification, DataDefinition, Dataset, Report
from evidently.presets import ClassificationPreset, DataDriftPreset


def _definition(numeric: list[str], categorical: list[str], with_labels: bool) -> DataDefinition:
    clf = [BinaryClassification(target="target", prediction_probas="score")] if with_labels else None
    return DataDefinition(
        numerical_columns=numeric + ["score"], categorical_columns=categorical + (["target"] if with_labels else []), classification=clf
    )


def _prep(df: pd.DataFrame, numeric: list[str], categorical: list[str], with_labels: bool) -> pd.DataFrame:
    cols = numeric + categorical + ["score"] + (["target"] if with_labels else [])
    out = df[cols].copy()
    for c in categorical:
        out[c] = out[c].astype(str)
    for c in numeric + ["score"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")
    if with_labels:
        out["target"] = out["target"].astype(int)
    return out


def drift_report(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
    out_html: Path,
    method: str | None = "psi",
    with_labels: bool = True,
    title: str = "",
) -> dict[str, Any]:
    """Gera o HTML do Evidently e devolve o resumo por coluna (drift sim/não + score)."""
    dd = _definition(numeric, categorical, with_labels)
    ref = Dataset.from_pandas(_prep(reference, numeric, categorical, with_labels), data_definition=dd)
    cur = Dataset.from_pandas(_prep(current, numeric, categorical, with_labels), data_definition=dd)
    drift_cols = numeric + categorical + ["score"]
    metrics: list = [DataDriftPreset(columns=drift_cols, method=method)] if method else [DataDriftPreset(columns=drift_cols)]
    if with_labels:
        metrics.append(ClassificationPreset())
    snapshot = Report(metrics, metadata={"title": title} if title else None).run(current_data=cur, reference_data=ref)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    snapshot.save_html(str(out_html))
    return summarize(snapshot.dict())


def summarize(snap: dict[str, Any]) -> dict[str, Any]:
    """Extrai, de cada ValueDrift, o score do teste e se ultrapassou o limiar do método."""
    columns: dict[str, dict[str, Any]] = {}
    drifted_share = drifted_count = None
    for m in snap.get("metrics", []):
        name = str(m.get("metric_name", ""))
        value = m.get("value")
        if name.startswith("ValueDrift"):
            col = re.search(r"column=([^,)]+)", name).group(1)
            method = re.search(r"method=([^,)]+)", name)
            thr = re.search(r"threshold=([^,)]+)", name)
            method_s = method.group(1) if method else "auto"
            threshold = float(thr.group(1)) if thr else None
            # Métodos de distância (psi, wasserstein, jensenshannon): drift se score >= limiar.
            # Testes de hipótese (ks, chisquare, z): drift se p-valor < limiar.
            is_pvalue = any(k in method_s.lower() for k in ("ks", "chi", "z-test", "p_value", "fisher", "anderson"))
            detected = None if threshold is None else (value < threshold if is_pvalue else value >= threshold)
            columns[col] = {"method": method_s, "score": value, "threshold": threshold, "drift_detected": detected}
        elif name.startswith("DriftedColumnsCount") and isinstance(value, dict):
            drifted_share, drifted_count = value.get("share"), value.get("count")
        elif name.startswith(("RocAuc", "Accuracy", "Recall", "Precision", "LogLoss")):
            columns.setdefault("_classification", {})[name.split("(")[0]] = value
    return {"columns": columns, "drifted_share": drifted_share, "drifted_count": drifted_count}
