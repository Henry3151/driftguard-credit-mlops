"""Estimativa de desempenho SEM rótulo (NannyML CBPE) — componente opcional.

Útil porque, em crédito, a inadimplência real só é conhecida meses depois (label delay).
"""

from __future__ import annotations

import pandas as pd


def estimate_auc(reference: pd.DataFrame, current: pd.DataFrame, chunk_size: int = 5000) -> float | None:
    from driftguard.adapters import available, note_skipped

    if not available("nannyml"):
        note_skipped("nannyml", "pacote não instalado (pip install .[extras])")
        return None
    import nannyml as nml  # pragma: no cover

    est = nml.CBPE(  # pragma: no cover
        y_pred_proba="score",
        y_pred="prediction",
        y_true="target",
        problem_type="classification_binary",
        metrics=["roc_auc"],
        chunk_size=chunk_size,
    )
    est.fit(reference[["score", "prediction", "target"]])  # pragma: no cover
    res = est.estimate(current[["score", "prediction"]]).to_df()  # pragma: no cover
    return float(res[("roc_auc", "value")].mean())  # pragma: no cover
