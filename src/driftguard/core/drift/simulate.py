"""Simulador de degradação temporal (6 janelas mensais de produção).

Mecanismos (configs/drift_scenario.yaml):
1. Covariate shift por REAMOSTRAGEM PONDERADA da carteira de produção: muda P(X)
   preservando P(Y|X) — drift de população "puro" (inflação, endividamento, público jovem).
2. Drift multivariado: em parte da carteira as condições do empréstimo (crédito, parcela,
   bem) ficam desacopladas da renda — marginais quase iguais, relação conjunta muda.
3. Concept drift: no segmento vulnerável, bons pagadores passam a inadimplir com
   probabilidade crescente — P(Y|X) muda com X igual.
4. Controles: variáveis não alteradas diretamente.

O "gabarito" (rótulo original, segmento, linhas desacopladas) é salvo à parte em
data/production/_ground_truth/ e usado SOMENTE para validar a análise causal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from driftguard.core.data.canonical import add_derived_features

LOAN_TERMS = ["credit_amount", "annuity", "goods_price"]


def reference_profile(ref: pd.DataFrame, scenario: dict) -> dict[str, Any]:
    feats = add_derived_features(ref)
    seg = scenario["concept_drift"]["segment"]
    prof = {"mean": {}, "std": {}}
    for c in scenario["covariate_shift"]:
        x = np.log1p(feats[c]) if c == "income" else feats[c]
        prof["mean"][c], prof["std"][c] = float(x.mean()), float(x.std())
    prof["dti_threshold"] = float(feats["debt_to_income"].quantile(seg["debt_to_income_quantile_gte"]))
    prof["income_threshold"] = float(feats["income"].quantile(seg["income_quantile_lte"]))
    prof["income_mean"] = float(feats["income"].mean())
    return prof


def _weights(pool: pd.DataFrame, t: float, coefs: dict[str, float], prof: dict) -> np.ndarray:
    feats = add_derived_features(pool)
    logit = np.zeros(len(pool))
    for c, coef in coefs.items():
        x = np.log1p(feats[c]) if c == "income" else feats[c]
        z = ((x - prof["mean"][c]) / prof["std"][c]).clip(-4, 4).to_numpy()
        logit += coef * z
    w = np.exp(t * logit)
    return w / w.sum()


def simulate_windows(
    pool: pd.DataFrame, pool_audit: pd.DataFrame, ref: pd.DataFrame, scenario: dict, out_dir: Path
) -> list[dict[str, Any]]:
    rng = np.random.default_rng(scenario["seed"])
    prof = reference_profile(ref, scenario)
    n_win = scenario["n_windows"]
    size = min(scenario["window_size"], len(pool) // 2)  # amostras pequenas (modo demo/testes)
    months = pd.period_range(scenario["start_month"], periods=n_win, freq="M").astype(str)
    gt_dir = out_dir / "_ground_truth"
    audit_dir = out_dir.parent / "audit"
    for d in (out_dir, gt_dir, audit_dir):
        d.mkdir(parents=True, exist_ok=True)
    audit_idx = pool_audit.set_index("customer_id")
    manifest = []

    for k in range(1, n_win + 1):
        t = k / n_win
        idx = rng.choice(len(pool), size=size, replace=False, p=_weights(pool, t, scenario["covariate_shift"], prof))
        win = pool.iloc[idx].reset_index(drop=True).copy()

        # (2) desacoplamento crédito x renda numa fração crescente da carteira
        frac = t * scenario["multivariate"]["decouple_credit_from_income_max_fraction"]
        dec = rng.random(size) < frac
        if dec.sum() > 1:
            perm = rng.permutation(np.flatnonzero(dec))
            win.loc[dec, LOAN_TERMS] = win.loc[perm, LOAN_TERMS].to_numpy()

        # (3) concept drift no segmento vulnerável
        feats = add_derived_features(win)
        segment = (feats["debt_to_income"] >= prof["dti_threshold"]) & (feats["income"] <= prof["income_threshold"])
        flip_p = t * scenario["concept_drift"]["max_flip_probability"]
        target_original = win["target"].astype(int).to_numpy()
        flips = segment.to_numpy() & (target_original == 0) & (rng.random(size) < flip_p)
        win["target"] = np.where(flips, 1, target_original)

        name = f"window_{k:02d}"
        win.to_parquet(out_dir / f"{name}.parquet", index=False)
        audit_idx.loc[win["customer_id"]].reset_index().to_parquet(audit_dir / f"{name}.parquet", index=False)
        pd.DataFrame(
            {
                "customer_id": win["customer_id"],
                "target_original": target_original,
                "in_segment": segment.to_numpy(),
                "decoupled": dec,
                "flipped": flips,
            }
        ).to_parquet(gt_dir / f"{name}.parquet", index=False)
        manifest.append(
            {
                "window": name,
                "month": months[k - 1],
                "intensity": round(t, 3),
                "rows": size,
                "decoupled_rows": int(dec.sum()),
                "segment_rows": int(segment.sum()),
                "label_flips": int(flips.sum()),
                "default_rate": round(float(win["target"].mean()), 4),
                "default_rate_original": round(float(target_original.mean()), 4),
            }
        )

    (out_dir / "scenario.json").write_text(
        json.dumps({"scenario": scenario, "reference_profile": prof, "windows": manifest}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest
