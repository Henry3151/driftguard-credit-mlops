"""Estatísticas de drift implementadas e testadas no próprio projeto (ADR-0005).

- PSI: tamanho da mudança (bins pelos quantis da referência).
- KS / Qui-quadrado: significância, com correção de Benjamini-Hochberg e efeito mínimo.
- MMD (kernel RBF + permutação): drift multivariado (mudança de correlação).
- ADWIN (River): ponto de mudança no fluxo de scores.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

EPS = 1e-6


def _as_category(s: pd.Series) -> pd.Series:
    """Representação canônica de categorias: 2, 2.0 e "2" viram a mesma categoria."""
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce").astype(float).map(lambda v: f"{v:g}")
    return s.astype(str)


def psi(reference: pd.Series, current: pd.Series, bins: int = 10) -> float:
    """Population Stability Index para variável numérica ou categórica."""
    ref = pd.Series(reference).dropna()
    cur = pd.Series(current).dropna()
    if ref.empty or cur.empty:
        return float("nan")
    if pd.api.types.is_numeric_dtype(ref) and ref.nunique() > bins:
        edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
        edges[0], edges[-1] = -np.inf, np.inf
        p = np.histogram(ref, bins=edges)[0] / len(ref)
        q = np.histogram(cur, bins=edges)[0] / len(cur)
    else:
        ref, cur = _as_category(ref), _as_category(cur)
        cats = sorted(set(ref) | set(cur))
        p = ref.value_counts(normalize=True).reindex(cats, fill_value=0).to_numpy()
        q = cur.value_counts(normalize=True).reindex(cats, fill_value=0).to_numpy()
    p, q = np.clip(p, EPS, None), np.clip(q, EPS, None)
    return float(np.sum((q - p) * np.log(q / p)))


def ks_test(reference: pd.Series, current: pd.Series) -> tuple[float, float]:
    r = stats.ks_2samp(pd.Series(reference).dropna(), pd.Series(current).dropna())
    return float(r.statistic), float(r.pvalue)


def chi2_test(reference: pd.Series, current: pd.Series) -> tuple[float, float]:
    ref, cur = _as_category(pd.Series(reference)), _as_category(pd.Series(current))
    cats = sorted(set(ref) | set(cur))
    table = np.vstack([ref.value_counts().reindex(cats, fill_value=0), cur.value_counts().reindex(cats, fill_value=0)])
    table = table[:, table.sum(axis=0) > 0]
    chi2, p, _, _ = stats.chi2_contingency(table)
    # Cramér's V como tamanho de efeito (comparável ao D do KS)
    v = float(np.sqrt(chi2 / (table.sum() * (min(table.shape) - 1))))
    return v, float(p)


def benjamini_hochberg(pvalues: list[float], alpha: float) -> list[bool]:
    p = np.asarray(pvalues, dtype=float)
    n = len(p)
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n, dtype=bool)
    out[order] = adj <= alpha
    return out.tolist()


def mmd_permutation_test(X: np.ndarray, Y: np.ndarray, n_perm: int = 200, seed: int = 0) -> tuple[float, float]:
    """MMD² não-viesado com kernel RBF (heurística da mediana) e p-valor por permutação."""
    rng = np.random.default_rng(seed)
    Z = np.vstack([X, Y])
    sq = np.sum(Z**2, axis=1)
    D = np.maximum(sq[:, None] + sq[None, :] - 2 * Z @ Z.T, 0)
    sigma2 = np.median(D[np.triu_indices_from(D, k=1)]) or 1.0
    K = np.exp(-D / sigma2)
    n = len(X)

    def stat(idx: np.ndarray) -> float:
        a, b = idx[:n], idx[n:]
        kxx, kyy, kxy = K[np.ix_(a, a)], K[np.ix_(b, b)], K[np.ix_(a, b)]
        m = len(b)
        return float((kxx.sum() - np.trace(kxx)) / (n * (n - 1)) + (kyy.sum() - np.trace(kyy)) / (m * (m - 1)) - 2 * kxy.mean())

    base = np.arange(len(Z))
    observed = stat(base)
    perms = [stat(rng.permutation(base)) for _ in range(n_perm)]
    pvalue = (1 + sum(v >= observed for v in perms)) / (n_perm + 1)
    return observed, float(pvalue)


def adwin_change_points(values: np.ndarray, delta: float = 0.002) -> list[int]:
    from river import drift

    det = drift.ADWIN(delta=delta)
    points = []
    for i, v in enumerate(values):
        det.update(float(v))
        if det.drift_detected:
            points.append(i)
    return points


def severity(psi_value: float, slo: dict) -> str:
    if np.isnan(psi_value):
        return "n/a"
    if psi_value >= slo["drift"]["psi_severe"]:
        return "severo"
    if psi_value >= slo["drift"]["psi_moderate"]:
        return "moderado"
    return "estavel"


def feature_drift_table(
    ref: pd.DataFrame, cur: pd.DataFrame, numeric: list[str], categorical: list[str], slo: dict, importance: dict[str, float]
) -> pd.DataFrame:
    d = slo["drift"]
    rows = []
    for c in numeric + categorical:
        is_num = c in numeric
        p = psi(ref[c], cur[c], bins=d["psi_bins"])
        eff, pval = ks_test(ref[c], cur[c]) if is_num else chi2_test(ref[c], cur[c])
        rows.append(
            {
                "feature": c,
                "type": "numerica" if is_num else "categorica",
                "test": "KS" if is_num else "Qui2",
                "psi": round(p, 5),
                "effect_size": round(eff, 4),
                "p_value": pval,
                "importance": importance.get(c, 0.0),
            }
        )
    df = pd.DataFrame(rows)
    df["significant_bh"] = benjamini_hochberg(df["p_value"].tolist(), d["ks_alpha"])
    df["drift_detected"] = (df["significant_bh"] & (df["effect_size"] >= d["ks_min_effect"])) | (df["psi"] >= d["psi_moderate"])
    df["severity"] = df["psi"].map(lambda v: severity(v, slo))
    df.loc[df["drift_detected"] & (df["severity"] == "estavel"), "severity"] = "leve"
    df["important"] = df["importance"] >= d["important_feature_share"]
    df["impact_score"] = (df["psi"] * df["importance"]).round(5)
    return df.sort_values("impact_score", ascending=False).reset_index(drop=True)
