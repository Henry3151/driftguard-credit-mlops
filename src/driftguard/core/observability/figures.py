"""Figuras estáticas (PNG) para documentação, geradas a partir dos relatórios do pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

BLUE, RED, TEAL, PURPLE, AMBER, GREEN, GREY = "#2563eb", "#c0392b", "#0891b2", "#7c3aed", "#b7791f", "#1a7f37", "#6b7280"
plt.rcParams.update(
    {
        "figure.dpi": 130,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "font.size": 10,
        "axes.titleweight": "bold",
        "axes.titlesize": 12,
    }
)


def _save(fig, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def build_all(root: Path, out_dir: Path, slo: dict) -> list[Path]:
    rep = root / "reports"
    wm = pd.read_csv(rep / "monitoring" / "window_metrics.csv")
    drift = pd.read_csv(rep / "drift" / "drift_summary.csv")
    meta = json.loads((root / "models" / "champion" / "metadata.json").read_text(encoding="utf-8"))
    base = meta["baseline_test"]
    months = dict(zip(wm["window"], wm["month"]))
    outs: list[Path] = []

    # 1) Heatmap de PSI
    piv = drift.pivot_table(index="feature", columns="window", values="psi")
    piv = piv.loc[piv.iloc[:, -1].sort_values().index]
    fig, ax = plt.subplots(figsize=(9, 6.2))
    im = ax.imshow(piv.values, cmap="YlOrRd", aspect="auto", vmin=0, vmax=max(0.3, float(piv.values.max())))
    ax.set_xticks(range(piv.shape[1]), [months[c] for c in piv.columns])
    ax.set_yticks(range(piv.shape[0]), piv.index)
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            v = piv.values[i, j]
            ax.text(j, i, f"{v:.3f}", ha="center", va="center", fontsize=7, color="white" if v > 0.3 else "#111")
    ax.grid(False)
    fig.colorbar(im, ax=ax, label="PSI (≥0,10 moderado · ≥0,25 severo)")
    ax.set_title("PSI por feature × mês de produção")
    outs.append(_save(fig, out_dir / "psi_heatmap.png"))

    # 2) Degradação do modelo
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.2))
    a1.plot(wm["month"], wm["auc"], "-o", color=BLUE, lw=2.5, label="AUC")
    a1.plot(wm["month"], wm["ks"], "-o", color=TEAL, lw=2, label="KS")
    a1.axhline(base["auc"], color=GREY, ls="--", lw=1, label=f"AUC referência ({base['auc']:.3f})")
    a1.axhline(base["auc"] - slo["model"]["auc_max_drop"], color=RED, ls=":", lw=1.5, label="SLO AUC")
    a1.set_title("Desempenho do modelo por mês")
    a1.legend(fontsize=8, loc="lower left")
    a2.plot(wm["month"], wm["default_rate"] * 100, "-o", color=RED, lw=2.5, label="Inadimplência real")
    a2.plot(wm["month"], wm["mean_pd"] * 100, "-o", color=BLUE, lw=2, label="PD média prevista")
    a2.fill_between(wm["month"], wm["mean_pd"] * 100, wm["default_rate"] * 100, color=RED, alpha=0.12, label="Risco subestimado")
    a2.set_ylabel("%")
    a2.set_title("Risco real × risco previsto")
    a2.legend(fontsize=8, loc="upper left")
    outs.append(_save(fig, out_dir / "model_degradation.png"))

    # 3) Distribuições: referência × último mês
    ref = pd.read_parquet(root / "data" / "reference" / "monitoring_reference.parquet")
    last_w = wm["window"].iloc[-1]
    from driftguard.core.data.canonical import add_derived_features

    cur = add_derived_features(pd.read_parquet(root / "data" / "production" / f"{last_w}.parquet"))
    feats = [
        ("income", "Renda", True),
        ("debt_to_income", "Parcela / renda", False),
        ("age_years", "Idade (anos)", False),
        ("children_count", "Nº de filhos (controle)", False),
    ]
    fig, axes = plt.subplots(1, 4, figsize=(15, 3.6))
    last_drift = drift[drift["window"] == last_w].set_index("feature")
    for ax, (f, label, log) in zip(axes, feats):
        r, c = ref[f].dropna().astype(float), cur[f].dropna().astype(float)
        if log:
            r, c = np.log10(r), np.log10(c)
            label += " (log10)"
        hi = np.percentile(np.r_[r, c], 99.5)
        bins = np.linspace(min(r.min(), c.min()), hi, 40) if f != "children_count" else np.arange(-0.5, 6.5, 1)
        ax.hist(r, bins=bins, density=True, alpha=0.55, color=BLUE, label="Referência")
        ax.hist(c, bins=bins, density=True, alpha=0.55, color=RED, label=months[last_w])
        d = last_drift.loc[f]
        ax.set_title(
            f"{label}\nPSI={d['psi']:.3f} · {'DRIFT' if d['drift_detected'] else 'estável'}",
            fontsize=10,
            color=RED if d["drift_detected"] else GREEN,
        )
        ax.legend(fontsize=7)
    outs.append(_save(fig, out_dir / "drift_distributions.png"))

    # 4) Impacto de negócio
    fig, ax = plt.subplots(figsize=(9, 3.8))
    x = np.arange(len(wm))
    ax.bar(x - 0.2, wm["expected_loss"] / 1e6, 0.4, color=BLUE, label="Perda esperada pelo modelo")
    ax.bar(x + 0.2, wm["realized_loss"] / 1e6, 0.4, color=RED, label="Perda realizada")
    for i, g in enumerate(wm["loss_gap"]):
        ax.text(i + 0.2, wm["realized_loss"].iloc[i] / 1e6, f"+{g:.0%}", ha="center", va="bottom", fontsize=8, color=RED)
    ax.set_xticks(x, wm["month"])
    ax.set_ylabel("R$ milhões")
    ax.set_title("Impacto de negócio na carteira aprovada")
    ax.legend(fontsize=8)
    outs.append(_save(fig, out_dir / "business_impact.png"))

    # 5) Causalidade
    causal = json.loads((rep / "causal" / "attribution.json").read_text(encoding="utf-8"))
    pw = pd.DataFrame(causal["per_window"])
    fig, ax = plt.subplots(figsize=(9, 3.8))
    ax.bar(pw["month"], pw["efeito_covariate_shift_auc"], color=TEAL, label="Mudança de população (covariate shift)")
    ax.bar(
        pw["month"],
        pw["efeito_concept_drift_auc"],
        bottom=np.minimum(pw["efeito_covariate_shift_auc"], 0),
        color=RED,
        label="Mudança de conceito (concept drift)",
    )
    ax.axhline(0, color="#111", lw=0.8)
    ax.set_ylabel("Δ AUC vs referência")
    ax.set_title("Decomposição causal da perda de AUC")
    ax.legend(fontsize=8, loc="lower left")
    outs.append(_save(fig, out_dir / "causal_decomposition.png"))

    # 6) Contrato bloqueando o lote defeituoso
    bad = json.loads((rep / "validation" / "gate1_contract_bad_batch.json").read_text(encoding="utf-8"))
    items = sorted(bad["rule_failures"].items(), key=lambda kv: kv[1])
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.barh([k.replace("credit_application@1.0.0::", "schema::") for k, _ in items], [v for _, v in items], color=RED)
    for i, (_, v) in enumerate(items):
        ax.text(v, i, f" {v}", va="center", fontsize=8)
    ax.set_title(f"Lote defeituoso BLOQUEADO — {bad['n_invalid_rows']} linhas inválidas ({bad['invalid_fraction']:.1%})")
    ax.set_xlabel("violações")
    outs.append(_save(fig, out_dir / "contract_violations.png"))

    # 7) Correções avaliadas
    chal = json.loads((rep / "model" / "challenger_evaluation.json").read_text(encoding="utf-8"))
    opts = pd.DataFrame(chal["response_options"])
    if len(opts):
        labels = [o.split(" (")[0] for o in opts["opcao"]]
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 3.8))
        colors = [GREY, AMBER, PURPLE, GREEN]
        a1.barh(labels, opts["realized_loss"] / 1e6, color=colors)
        for i, v in enumerate(opts["realized_loss"] / 1e6):
            a1.text(v, i, f" R$ {v:,.0f} mi".replace(",", "."), va="center", fontsize=8)
        a1.invert_yaxis()
        a1.set_title(f"Perda realizada por resposta ({months.get(chal['holdout_window'], chal['holdout_window'])})")
        w = 0.38
        y = np.arange(len(opts))
        a2.barh(y - w / 2, opts["approval_rate"] * 100, w, color=BLUE, label="Aprovação (%)")
        a2.barh(y + w / 2, opts["recall_default"] * 100, w, color=RED, label="Inadimplentes barrados (%)")
        a2.set_yticks(y, labels)
        a2.invert_yaxis()
        a2.set_title("Trade-off aprovação × proteção")
        a2.legend(fontsize=8, loc="lower right")
        outs.append(_save(fig, out_dir / "response_options.png"))
    return outs
