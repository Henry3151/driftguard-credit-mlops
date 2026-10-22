"""Orquestração dos estágios do pipeline (chamados pela CLI, Makefile, CI ou Airflow).

Cada estágio é idempotente, lê/grava artefatos em disco e é instrumentado com
logs JSON, métricas Prometheus e (quando aplicável) runs no MLflow.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from driftguard.core.causal import attribution
from driftguard.core.contracts.gates import IngestionBlocked, run_gate
from driftguard.core.contracts.loader import build_schema, feature_schema, load_contract
from driftguard.core.data import leakage, synthetic
from driftguard.core.data.canonical import add_derived_features, canonicalize, read_source
from driftguard.core.data.split import split_reference_production, split_train_valid_test
from driftguard.core.drift import evidently_reports, policy, simulate, stats
from driftguard.core.governance import fairness
from driftguard.core.governance.retention import apply_retention
from driftguard.core.model import explain
from driftguard.core.model import train as tr
from driftguard.core.observability import metrics, tracking
from driftguard.core.observability.alerts import Alert, AlertManager
from driftguard.core.observability.logging import RUN, log_event, stage
from driftguard.core.privacy.pii_scan import scan_files
from driftguard.core.privacy.pseudonymize import age_band, apply_privacy
from driftguard.settings import get_settings

PRODUCTION_SHARE = 0.40
REFERENCE_SAMPLE = 20_000


# ----------------------------------------------------------------------------- utilidades
def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _gate2_schema():
    import pandera.pandas as pa

    cfg = get_settings()
    schema = feature_schema(cfg.numeric_features, cfg.categorical_features)
    return schema.add_columns(
        {
            "customer_id": pa.Column("string", nullable=False, unique=True, coerce=True),
            "target": pa.Column("Int64", nullable=True, required=False, coerce=True),
        }
    )


def _gates_1_2(
    df_canonical: pd.DataFrame, batch: str, require_target: bool, reference_stats: dict | None = None, raise_on_block: bool = True
) -> tuple[pd.DataFrame, dict]:
    """Gate 1 (contrato) -> Gate 2 (features). Retorna o lote aprovado com features derivadas."""
    cfg = get_settings()
    contract = load_contract(cfg.contract_path)
    vdir = cfg.paths.reports / "validation"
    ok, r1 = run_gate(
        df_canonical.reset_index(drop=True),
        contract,
        cfg.slo,
        "gate1_contract",
        batch,
        cfg.paths.quarantine,
        vdir,
        require_target=require_target,
        reference_stats=reference_stats,
        raise_on_block=raise_on_block,
    )
    if r1.blocked:
        return ok, {"gate1": r1.__dict__}
    feats = add_derived_features(ok)
    ok2, r2 = run_gate(
        feats.reset_index(drop=True),
        contract,
        cfg.slo,
        "gate2_features",
        batch,
        cfg.paths.quarantine,
        vdir,
        schema=_gate2_schema(),
        raise_on_block=raise_on_block,
    )
    return ok2, {"gate1": r1.__dict__, "gate2": r2.__dict__}


def _missingness_report(df: pd.DataFrame, target: str) -> dict[str, Any]:
    """Diagnóstico MCAR/MAR/MNAR: a ausência está associada ao alvo? (justifica o tratamento)."""
    from scipy.stats import chi2_contingency

    out = {}
    for c in df.columns:
        miss = df[c].isna()
        if c == target or miss.sum() == 0:
            continue
        table = pd.crosstab(miss, df[target])
        p = chi2_contingency(table)[1] if table.shape == (2, 2) else np.nan
        out[c] = {
            "null_rate": round(float(miss.mean()), 4),
            "default_rate_when_null": round(float(df.loc[miss, target].mean()), 4),
            "default_rate_when_present": round(float(df.loc[~miss, target].mean()), 4),
            "chi2_p_value": float(p),
            "diagnostico": "não-MCAR (ausência informativa)" if p < 0.01 else "compatível com MCAR",
        }
    return out


# ----------------------------------------------------------------------------- qualidade de dados
def ingest(synthetic_rows: int | None = None) -> dict[str, Any]:
    cfg = get_settings()
    cfg.paths.ensure()
    with stage("ingest", source="synthetic" if synthetic_rows else cfg.dataset["name"]) as ctx:
        raw = synthetic.synthetic_applications(synthetic_rows) if synthetic_rows else read_source()
        canon = canonicalize(raw)
        ctx["rows"] = len(canon)
        _write_json(
            cfg.paths.reports / "validation" / "missingness_diagnostics.json",
            _missingness_report(canon.drop(columns=["customer_id"]), "target"),
        )
        contract = load_contract(cfg.contract_path)
        vdir = cfg.paths.reports / "validation"
        ok, r1 = run_gate(canon, contract, cfg.slo, "gate1_contract", "source", cfg.paths.quarantine, vdir, require_target=True)

    with stage("privacy") as ctx:
        model_tbl, audit = apply_privacy(ok, "customer_id", cfg.dataset["sensitive_attributes"], cfg.pseudonym_salt)
        ctx["rows"] = len(model_tbl)
        log_event("PII_TREATED", pseudonymized="customer_id", segregated=cfg.dataset["sensitive_attributes"])

    with stage("features") as ctx:
        feats = add_derived_features(model_tbl)
        _, r2 = run_gate(feats, contract, cfg.slo, "gate2_features", "source", cfg.paths.quarantine, vdir, schema=_gate2_schema())
        ctx["rows"] = len(feats)
        gates = {"gate1": r1.__dict__, "gate2": r2.__dict__}

    with stage("split") as ctx:
        contract_cols = [c for c in load_contract(cfg.contract_path).fields if c in model_tbl.columns]
        base = model_tbl[contract_cols]
        ref, pool = split_reference_production(base, "target", PRODUCTION_SHARE, cfg.seed)
        splits = split_train_valid_test(ref, "target", cfg.seed)
        cfg.paths.reference.mkdir(parents=True, exist_ok=True)
        for name, part in splits.items():
            part.to_parquet(cfg.paths.reference / f"{name}.parquet", index=False)
        pool.to_parquet(cfg.paths.reference / "production_pool.parquet", index=False)
        aud = audit.set_index("customer_id")
        aud.loc[ref["customer_id"]].reset_index().to_parquet(cfg.paths.audit / "reference.parquet", index=False)
        aud.loc[pool["customer_id"]].reset_index().to_parquet(cfg.paths.audit / "production_pool.parquet", index=False)
        leak = leakage.leakage_report(ref, pool, "customer_id", [c for c in cfg.numeric_features if c in ref], cfg.categorical_features)
        _write_json(cfg.paths.reports / "validation" / "leakage_reference_vs_production.json", leak)
        ctx["rows"] = len(base)
        log_event(
            "SPLIT_DONE",
            reference_rows=len(ref),
            production_pool_rows=len(pool),
            leakage=leak,
            status="success" if leak["passed"] else "failed",
            level="INFO" if leak["passed"] else "ERROR",
        )
        if not leak["passed"]:
            raise RuntimeError("Vazamento entre referência e produção detectado.")
    return {"gates": gates, "reference_rows": len(ref), "pool_rows": len(pool), "leakage": leak}


def validate_batch(path: Path | None = None, bad: bool = False) -> int:
    """Valida um lote externo. Exit code 0 = aceito, 2 = bloqueado."""
    cfg = get_settings()
    cfg.paths.ensure()
    with stage("validate_batch", batch=path.name if path else "bad_batch") as ctx:
        if bad:
            df, injected = synthetic.bad_batch()
            out = cfg.paths.data / "bad_batch.csv"
            df.to_csv(out, index=False)
            log_event("BAD_BATCH_GENERATED", file="data/bad_batch.csv", injected_errors=injected, rows=len(df))
            name = "bad_batch"
        else:
            df, name = pd.read_csv(path), path.stem
        ctx["rows"] = len(df)
        _gates_1_2(canonicalize(df), name, require_target=False)
    return 0


# ----------------------------------------------------------------------------- treino
def _load_split(name: str) -> pd.DataFrame:
    return pd.read_parquet(get_settings().paths.reference / f"{name}.parquet")


def train() -> dict[str, Any]:
    import mlflow
    from mlflow.models import infer_signature
    from mlflow.tracking import MlflowClient

    cfg = get_settings()
    num, cat = cfg.numeric_features, cfg.categorical_features
    lgd, margin = cfg.costs["lgd"], cfg.costs["margin"]
    with stage("train") as ctx:
        parts = {k: add_derived_features(_load_split(k)) for k in ("train", "valid", "test")}
        X = {k: tr.to_model_frame(v, num, cat) for k, v in parts.items()}
        y = {k: v["target"].astype(int).to_numpy() for k, v in parts.items()}
        credit = {k: v["credit_amount"].to_numpy(float) for k, v in parts.items()}
        ctx["rows"] = len(X["train"])

        pipe = tr.build_pipeline(num, cat, cfg.seed)
        t0 = time.perf_counter()
        pipe.fit(X["train"], y["train"])
        fit_s = time.perf_counter() - t0
        p = {k: pipe.predict_proba(X[k])[:, 1] for k in X}
        threshold, curve = tr.cost_optimal_threshold(y["valid"], p["valid"], credit["valid"], lgd, margin)
        m_valid = tr.evaluate(y["valid"], p["valid"], credit["valid"], threshold, lgd, margin)
        m_test = tr.evaluate(y["test"], p["test"], credit["test"], threshold, lgd, margin)
        importance = tr.feature_importance(pipe, num, cat)
        domain = tr.training_domain(X["train"], num, cat)

        # Baseline de fairness (auditoria com atributos segregados)
        aud = pd.read_parquet(cfg.paths.audit / "reference.parquet").set_index("customer_id")
        test_aud = aud.loc[parts["test"]["customer_id"]].reset_index(drop=True)
        test_aud["age_band"] = age_band(parts["test"]["age_years"].reset_index(drop=True))
        fair = fairness.fairness_summary(
            y["test"], p["test"], threshold, test_aud[["gender", "age_band"]], cfg.slo["model"]["min_group_size"]
        )
        out_dir = cfg.paths.reports / "model"
        out_dir.mkdir(parents=True, exist_ok=True)
        fair.to_json(out_dir / "fairness_baseline.json", orient="records", indent=2, force_ascii=False)
        curve.to_csv(out_dir / "threshold_cost_curve.csv", index=False)

        # Motivos da decisão (LGPD art. 20) para uma amostra de propostas recusadas
        rejected = np.flatnonzero(p["test"] >= threshold)[:200]
        reasons = explain.decision_reasons(pipe, X["test"].iloc[rejected], num, cat)
        reasons.insert(0, "pd_prevista", np.round(p["test"][rejected], 4))
        reasons.insert(0, "customer_id_pseudonimizado", parts["test"]["customer_id"].iloc[rejected].to_numpy())
        reasons.to_csv(out_dir / "decision_reasons_sample.csv", index=False)

        # Referência para monitoramento: amostra do TESTE (scores fora da amostra de treino)
        ref_mon = parts["test"].sample(min(REFERENCE_SAMPLE, len(parts["test"])), random_state=cfg.seed).copy()
        ref_mon["score"] = pipe.predict_proba(tr.to_model_frame(ref_mon, num, cat))[:, 1]
        ref_mon.to_parquet(cfg.paths.reference / "monitoring_reference.parquet", index=False)

        digests = {k: tr.dataset_digest(v) for k, v in parts.items()}
        with tracking.run(tracking.EXP_TRAINING, "xgboost-baseline", tags={"stage": "train", "model.role": "champion"}):
            mlflow.log_params(
                {
                    **{f"xgb.{k}": v for k, v in tr.XGB_PARAMS.items()},
                    "threshold": threshold,
                    "lgd": lgd,
                    "margin": margin,
                    "seed": cfg.seed,
                    "n_features": len(num + cat),
                    "contract": f"{load_contract(cfg.contract_path).id}@{load_contract(cfg.contract_path).version}",
                }
            )
            tracking.log_dict_flat("valid_", m_valid)
            tracking.log_dict_flat("test_", m_test)
            mlflow.log_metric("fit_seconds", fit_s)
            for k, v in parts.items():
                mlflow.log_input(
                    mlflow.data.from_pandas(v.drop(columns=["customer_id"]), name=f"reference_{k}", targets="target"), context=k
                )
            mlflow.log_dict(importance, "feature_importance.json")
            mlflow.log_dict(json.loads(fair.to_json(orient="records")), "fairness_baseline.json")
            tracking.log_artifacts_if_exist([out_dir / "threshold_cost_curve.csv"], "model_card")
            sig = infer_signature(X["train"].head(200), pipe.predict_proba(X["train"].head(200))[:, 1])
            info = tracking.log_sklearn_model(pipe, sig, X["train"].head(5), tracking.REGISTERED_MODEL)
            run_id = mlflow.active_run().info.run_id
        version = info.registered_model_version
        MlflowClient().set_registered_model_alias(tracking.REGISTERED_MODEL, "champion", version)

        meta = {
            "threshold": threshold,
            "baseline_valid": m_valid,
            "baseline_test": m_test,
            "importance": importance,
            "training_domain": domain,
            "numeric": num,
            "categorical": cat,
            "digests": digests,
            "mlflow_run_id": run_id,
            "registered_model": tracking.REGISTERED_MODEL,
            "model_version": version,
            "trained_run_id": RUN.run_id,
            "fit_seconds": round(fit_s, 2),
        }
        tr.save_bundle(cfg.paths.models / "champion", pipe, meta)
        _write_model_card(meta, fair)
        log_event(
            "MODEL_TRAINED",
            auc_test=m_test["auc"],
            ks_test=m_test["ks"],
            brier_test=m_test["brier"],
            threshold=threshold,
            model_version=version,
            alias="champion",
        )
    return meta


def _write_model_card(meta: dict, fair: pd.DataFrame) -> None:
    cfg = get_settings()
    t, v = meta["baseline_test"], meta["baseline_valid"]
    top = list(meta["importance"].items())[:8]
    fair_lines = "\n".join(
        f"| {r.attribute} | {r.disparate_impact:.3f} | {r.equal_opportunity_gap:.3f} | {r.auc_gap:.3f} | {r.least_approved_group} |"
        for r in fair.itertuples()
    )
    md = f"""# Model Card — Credit Scoring (XGBoost) · versão {meta["model_version"]}

> Gerado automaticamente pelo estágio `train` (run `{meta["trained_run_id"]}`, MLflow `{meta["mlflow_run_id"]}`).

## Uso pretendido
Estimar a probabilidade de inadimplência (PD) de propostas de crédito pessoal para apoiar a decisão
de concessão. **Não** deve ser usado para marketing, precificação discriminatória ou decisões sem
possibilidade de revisão humana (LGPD art. 20).

## Dados
- Fonte: Home Credit Default Risk (`application_train.csv`), projeção mínima de {len(meta["numeric"]) + len(meta["categorical"])} features.
- Referência pseudonimizada (HMAC-SHA256); atributos sensíveis (gênero, estado civil) **excluídos** das features.
- Hashes dos conjuntos: train `{meta["digests"]["train"][:16]}…`, valid `{meta["digests"]["valid"][:16]}…`, test `{meta["digests"]["test"][:16]}…`.

## Desempenho (referência)
| Conjunto | AUC | KS | Brier | Taxa de aprovação | Bad rate aprovados |
|---|---|---|---|---|---|
| Validação | {v["auc"]} | {v["ks"]} | {v["brier"]} | {v["approval_rate"]:.1%} | {v["bad_rate_approved"]:.2%} |
| Teste | {t["auc"]} | {t["ks"]} | {t["brier"]} | {t["approval_rate"]:.1%} | {t["bad_rate_approved"]:.2%} |

Limiar de decisão por custo esperado (LGD={cfg.costs["lgd"]}, margem={cfg.costs["margin"]}): **PD < {meta["threshold"]}** → aprova.

## Features mais importantes (ganho)
{chr(10).join(f"- `{k}`: {val:.1%}" for k, val in top)}

## Fairness (conjunto de teste)
| Atributo | Disparate impact (≥ 0,80) | Gap equal opportunity | Gap AUC | Grupo menos aprovado |
|---|---|---|---|---|
{fair_lines}

## Limitações
- Treinado em dados históricos de outro mercado: requer monitoramento contínuo de drift (ver dashboard).
- Scores de bureau externos têm ausência informativa (não-MCAR); o modelo trata nulos nativamente.
- Idade é usada como feature (prática comum em crédito), mas é monitorada por faixa etária na auditoria de vieses.
"""
    for path in (cfg.paths.reports / "model" / "MODEL_CARD.md",):
        path.write_text(md, encoding="utf-8")


# ----------------------------------------------------------------------------- drift e monitoramento
def simulate_drift() -> list[dict]:
    cfg = get_settings()
    with stage("simulate") as ctx:
        ref = pd.concat([_load_split(k) for k in ("train", "valid", "test")])
        pool = pd.read_parquet(cfg.paths.reference / "production_pool.parquet")
        pool_aud = pd.read_parquet(cfg.paths.audit / "production_pool.parquet")
        manifest = simulate.simulate_windows(pool, pool_aud, ref, cfg.scenario, cfg.paths.production)
        ctx["rows"] = sum(w["rows"] for w in manifest)
        for w in manifest:
            log_event("WINDOW_SIMULATED", **w)
    return manifest


def _predict_with_latency(pipe, X: pd.DataFrame, chunk: int = 1000) -> tuple[np.ndarray, dict[str, float]]:
    scores, lat = [], []
    for i in range(0, len(X), chunk):
        t0 = time.perf_counter()
        scores.append(pipe.predict_proba(X.iloc[i : i + chunk])[:, 1])
        dt = time.perf_counter() - t0
        lat.append(dt)
        metrics.PRED_LATENCY.observe(dt)
    ms = np.array(lat) * 1000
    return np.concatenate(scores), {
        "latency_p50_ms_1k": round(float(np.percentile(ms, 50)), 2),
        "latency_p95_ms_1k": round(float(np.percentile(ms, 95)), 2),
        "latency_p99_ms_1k": round(float(np.percentile(ms, 99)), 2),
    }


def _gate3_skew(X: pd.DataFrame, meta: dict) -> dict[str, Any]:
    """Gate 3: paridade de schema com o treino + taxa fora do domínio de treino."""
    dom = meta["training_domain"]
    expected = meta["numeric"] + meta["categorical"]
    if list(X.columns) != expected:
        raise IngestionBlocked("gate3_serving", f"schema da entrada do modelo difere do treino: {list(X.columns)}")
    out = {}
    for c, rng_ in dom["numeric"].items():
        x = X[c].dropna()
        out[c] = round(float(((x < rng_["p001"]) | (x > rng_["p999"])).mean()), 4) if len(x) else 0.0
    for c, cats in dom["categorical"].items():
        out[c] = round(float((~X[c].isin(cats)).mean()), 4)
    return out


def monitor() -> dict[str, Any]:
    import mlflow

    cfg = get_settings()
    num, cat = cfg.numeric_features, cfg.categorical_features
    pipe, meta = tr.load_bundle(cfg.paths.models / "champion")
    threshold, base = meta["threshold"], meta["baseline_test"]
    lgd, margin = cfg.costs["lgd"], cfg.costs["margin"]
    ref = pd.read_parquet(cfg.paths.reference / "monitoring_reference.parquet")
    scen = json.loads((cfg.paths.production / "scenario.json").read_text(encoding="utf-8"))
    ref_stats = {"income_mean": scen["reference_profile"]["income_mean"]}
    alerts = AlertManager(cfg.paths.reports / "alerts.jsonl", reset=True)
    drift_dir = cfg.paths.reports / "drift"
    drift_dir.mkdir(parents=True, exist_ok=True)
    window_rows, drift_frames, fair_rows, score_stream = [], [], [], [ref["score"].to_numpy()]
    delay = cfg.scenario["label_delay_windows"]
    months = {w["window"]: w["month"] for w in scen["windows"]}
    n_win = len(scen["windows"])

    for w in scen["windows"]:
        name = w["window"]
        with stage("monitor", window=name, month=w["month"]) as ctx:
            raw_win = pd.read_parquet(cfg.paths.production / f"{name}.parquet")
            ctx["rows"] = len(raw_win)
            ok, gates = _gates_1_2(raw_win, name, require_target=False, reference_stats=ref_stats, raise_on_block=False)
            g1 = gates["gate1"]
            if g1["decision"] == "blocked":
                alerts.fire(
                    Alert("DATA_QUALITY", "critical", f"Lote {name} bloqueado pelo contrato", name, {"reason": g1["blocked_reason"]})
                )
                continue
            if g1["decision"] == "accepted_with_quarantine":
                alerts.fire(Alert("DATA_QUALITY", "warning", f"{g1['n_invalid_rows']} linhas em quarentena", name, g1["rule_failures"]))
            for wn in g1["warnings"]:
                alerts.fire(
                    Alert("DATA_QUALITY", "info", f"Aviso de qualidade: {wn['rule']}", name, wn),
                    dedup_key=wn["rule"] + str(wn.get("column")),
                )

            X = tr.to_model_frame(ok, num, cat)
            skew = _gate3_skew(X, meta)
            skewed = {k: v for k, v in skew.items() if v > 0.05}
            if skewed:
                alerts.fire(Alert("DATA_DRIFT", "warning", "Entrada do modelo fora do domínio de treino (Gate 3)", name, skewed))
            score, lat = _predict_with_latency(pipe, X)
            cur = ok.copy()
            cur["score"] = score
            cur["prediction"] = (score >= threshold).astype(int)
            score_stream.append(score)

            # ---- drift univariado (PSI/KS/Qui2 + BH) ----------------------------
            dt = stats.feature_drift_table(ref, cur, num, cat, cfg.slo, meta["importance"])
            psi_score = stats.psi(ref["score"], cur["score"], bins=cfg.slo["drift"]["psi_bins"])
            dt.insert(0, "window", name)
            drift_frames.append(dt)
            for r in dt.itertuples():
                metrics.FEATURE_PSI.labels(r.feature, name).set(r.psi)
            n_drift = int(dt["drift_detected"].sum())
            metrics.DRIFTED_FEATURES.labels(name).set(n_drift)

            # ---- drift multivariado (MMD) ----------------------------------------
            mm_cols = [c for c in ["income", "credit_amount", "annuity", "age_years", "debt_to_income", "credit_to_income"]]
            rs = np.random.default_rng(cfg.seed)
            n_mmd = cfg.slo["drift"]["mmd_sample_size"]
            A = np.log1p(ref[mm_cols].to_numpy(float)[rs.choice(len(ref), n_mmd, replace=False)])
            B = np.log1p(cur[mm_cols].to_numpy(float)[rs.choice(len(cur), n_mmd, replace=False)])
            mu, sd = A.mean(0), A.std(0) + 1e-9
            mmd, mmd_p = stats.mmd_permutation_test((A - mu) / sd, (B - mu) / sd, cfg.slo["drift"]["mmd_permutations"], cfg.seed)
            mmd_sig = mmd_p < cfg.slo["drift"]["mmd_alpha"]
            metrics.MMD_PVALUE.labels(name).set(mmd_p)

            # ---- Evidently (relatório oficial) -----------------------------------
            ev = evidently_reports.drift_report(
                ref,
                cur,
                num,
                cat,
                drift_dir / f"evidently_{name}.html",
                method="psi",
                with_labels=True,
                title=f"Referência × Produção {months[name]}",
            )

            # ---- desempenho (rótulo atrasado) e negócio --------------------------
            yv = cur["target"].astype(int).to_numpy()
            perf = tr.evaluate(yv, score, cur["credit_amount"].to_numpy(float), threshold, lgd, margin)
            label_month = months.get(f"window_{int(name[-2:]) + delay:02d}", "após o fim da simulação")
            metrics.MODEL_AUC.labels(name).set(perf["auc"])
            metrics.MODEL_BRIER.labels(name).set(perf["brier"])
            metrics.EXPECTED_LOSS.labels(name).set(perf["expected_loss"])
            loss_gap = perf["realized_loss"] / perf["expected_loss"] - 1 if perf["expected_loss"] else 0.0

            # ---- fairness por janela ---------------------------------------------
            aud = (
                pd.read_parquet(cfg.paths.audit / f"{name}.parquet").set_index("customer_id").loc[cur["customer_id"]].reset_index(drop=True)
            )
            aud["age_band"] = age_band(cur["age_years"].reset_index(drop=True))
            fs = fairness.fairness_summary(yv, score, threshold, aud[["gender", "age_band"]], cfg.slo["model"]["min_group_size"])
            for r in fs.itertuples():
                metrics.DISPARATE_IMPACT.labels(r.attribute, name).set(r.disparate_impact)
                fair_rows.append(
                    {
                        "window": name,
                        "attribute": r.attribute,
                        "disparate_impact": r.disparate_impact,
                        "equal_opportunity_gap": r.equal_opportunity_gap,
                        "auc_gap": r.auc_gap,
                        "least_approved_group": r.least_approved_group,
                    }
                )
                protected = r.attribute in cfg.slo["model"]["fairness_protected_attributes"]
                if protected and r.disparate_impact < cfg.slo["model"]["fairness_min_disparate_impact"]:
                    alerts.fire(
                        Alert(
                            "FAIRNESS",
                            "warning",
                            f"Disparate impact abaixo de 0,80 em {r.attribute} (atributo protegido)",
                            name,
                            {"disparate_impact": r.disparate_impact, "grupo": r.least_approved_group},
                        )
                    )
                elif not protected and r.equal_opportunity_gap > cfg.slo["model"]["fairness_max_equal_opportunity_gap"]:
                    alerts.fire(
                        Alert(
                            "FAIRNESS",
                            "info",
                            f"Gap de equal opportunity elevado em {r.attribute} (monitorado)",
                            name,
                            {"equal_opportunity_gap": r.equal_opportunity_gap, "grupo": r.least_approved_group},
                        )
                    )

            # ---- política de resposta + alertas ----------------------------------
            decision = policy.decide(dt, perf, base, cfg.slo, mmd_sig)
            sev_feats = dt[dt["severity"].isin(["severo", "moderado"])]
            for r in sev_feats.itertuples():
                alerts.fire(
                    Alert(
                        "DATA_DRIFT",
                        "warning",
                        f"Drift {r.severity} em {r.feature}",
                        name,
                        {"psi": r.psi, "ks_or_v": r.effect_size, "importance": r.importance},
                    ),
                    dedup_key=r.feature,
                )
            if mmd_sig:
                alerts.fire(
                    Alert(
                        "MULTIVARIATE_DRIFT",
                        "warning",
                        "Mudança na relação conjunta entre variáveis (MMD)",
                        name,
                        {"mmd2": round(mmd, 5), "p_value": mmd_p},
                    )
                )
            if decision["performance_breach"]:
                composite = bool(decision["drifted_features"] or mmd_sig)
                alerts.fire(
                    Alert(
                        "CONCEPT_DRIFT",
                        "critical",
                        "Queda de AUC além do SLO" + (" + drift nos dados (alerta composto)" if composite else ""),
                        name,
                        {"auc": perf["auc"], "baseline": base["auc"], "drop": decision["auc_drop"]},
                        action=decision["action"],
                    )
                )
            if decision["calibration_breach"]:
                alerts.fire(
                    Alert(
                        "MODEL_CALIBRATION",
                        "warning",
                        "Brier acima do SLO (modelo descalibrado)",
                        name,
                        {"brier": perf["brier"], "ratio": decision["brier_ratio"]},
                    )
                )
            if loss_gap > 0.10:
                alerts.fire(
                    Alert(
                        "BUSINESS_IMPACT",
                        "critical" if loss_gap > 0.25 else "warning",
                        f"Perda realizada {loss_gap:.0%} acima da esperada",
                        name,
                        {"expected_loss": perf["expected_loss"], "realized_loss": perf["realized_loss"]},
                    )
                )

            row = {
                "window": name,
                "month": months[name],
                "intensity": w["intensity"],
                "rows": len(raw_win),
                "rows_accepted": len(ok),
                "rows_quarantined": g1["n_invalid_rows"],
                "dq_score": g1["dq_score"],
                "drifted_features": n_drift,
                "psi_score": round(psi_score, 4),
                "mmd2": round(mmd, 6),
                "mmd_p_value": mmd_p,
                "evidently_drifted_share": ev["drifted_share"],
                "label_available_month": label_month,
                **perf,
                **lat,
                "loss_gap": round(loss_gap, 4),
                "policy_level": decision["level"],
                "policy_action": decision["action"],
            }
            window_rows.append(row)
            _write_json(drift_dir / f"evidently_summary_{name}.json", ev)
            with tracking.run(
                tracking.EXP_MONITORING, f"monitor-{name}", tags={"window": name, "month": months[name], "policy_level": decision["level"]}
            ):
                mlflow.log_params(
                    {
                        "window": name,
                        "month": months[name],
                        "intensity": w["intensity"],
                        "threshold": threshold,
                        "model_version": meta["model_version"],
                    }
                )
                tracking.log_dict_flat("", {k: v for k, v in row.items() if isinstance(v, (int, float))})
                tracking.log_dict_flat("psi_", dict(zip(dt["feature"], dt["psi"])))
                mlflow.log_dict(json.loads(dt.to_json(orient="records")), "drift_table.json")
                mlflow.log_dict(skew, "gate3_skew.json")
            log_event(
                "WINDOW_MONITORED",
                window=name,
                auc=perf["auc"],
                drifted_features=n_drift,
                mmd_p=mmd_p,
                policy=decision["level"],
                alerts=len(alerts.fired),
            )

    with stage("monitor_summary") as ctx:
        drift_all = pd.concat(drift_frames, ignore_index=True)
        drift_all.to_csv(drift_dir / "drift_summary.csv", index=False)
        wm = pd.DataFrame(window_rows)
        (cfg.paths.reports / "monitoring").mkdir(parents=True, exist_ok=True)
        wm.to_csv(cfg.paths.reports / "monitoring" / "window_metrics.csv", index=False)
        pd.DataFrame(fair_rows).to_csv(cfg.paths.reports / "monitoring" / "fairness_by_window.csv", index=False)
        ctx["rows"] = len(wm)
        # ADWIN: ponto de mudança no fluxo de scores (referência seguida das janelas)
        stream = np.concatenate(score_stream)
        cps = stats.adwin_change_points(stream)
        bounds = np.cumsum([len(s) for s in score_stream])
        labels = ["referencia"] + [w["window"] for w in scen["windows"]]
        first = next((labels[int(np.searchsorted(bounds, cp, side="right"))] for cp in cps if cp >= bounds[0]), None)
        adwin = {
            "change_points": len(cps),
            "first_change_after_reference": first,
            "detections_by_segment": pd.Series([labels[int(np.searchsorted(bounds, c, side="right"))] for c in cps])
            .value_counts()
            .to_dict(),
        }
        _write_json(drift_dir / "adwin_score_stream.json", adwin)
        log_event("ADWIN_CHANGE_POINT", **adwin)
        # Relatório consolidado do Evidently com os testes padrão (KS / Qui-quadrado / Wasserstein)
        last = scen["windows"][-1]["window"]
        last_df = pd.read_parquet(cfg.paths.production / f"{last}.parquet")
        last_df = add_derived_features(last_df)
        last_df["score"] = pipe.predict_proba(tr.to_model_frame(last_df, num, cat))[:, 1]
        evidently_reports.drift_report(
            ref,
            last_df,
            num,
            cat,
            drift_dir / "evidently_consolidated_reference_vs_last_window.html",
            method=None,
            with_labels=True,
            title=f"Consolidado: referência × {months[last]} (testes padrão)",
        )
        metrics.sample_process()
    return {"windows": window_rows, "alerts": len(alerts.fired), "adwin": adwin, "n_windows": n_win}


# ----------------------------------------------------------------------------- resposta: challenger
def retrain_challenger() -> dict[str, Any]:
    """Retreino acionado pela política: challenger só é promovido se passar nos guardrails."""
    from mlflow.tracking import MlflowClient

    cfg = get_settings()
    num, cat = cfg.numeric_features, cfg.categorical_features
    lgd, margin = cfg.costs["lgd"], cfg.costs["margin"]
    wm = pd.read_csv(cfg.paths.reports / "monitoring" / "window_metrics.csv")
    with stage("retrain") as ctx:
        if not (wm["policy_level"] == "severo").any():
            log_event("RETRAIN_SKIPPED", reason="nenhuma janela com severidade 'severo'")
            return {"skipped": True}
        champion, meta = tr.load_bundle(cfg.paths.models / "champion")
        wins = sorted(cfg.paths.production.glob("window_*.parquet"))
        holdout = wins[-1]  # janela mais recente: avaliação
        labeled = wins[: -cfg.scenario["label_delay_windows"]]  # só janelas cujo rótulo já chegou
        train_df = pd.concat([_load_split("train")] + [pd.read_parquet(p) for p in labeled], ignore_index=True)
        train_df = add_derived_features(train_df)
        ctx["rows"] = len(train_df)
        chal = tr.build_pipeline(num, cat, cfg.seed)
        chal.fit(tr.to_model_frame(train_df, num, cat), train_df["target"].astype(int))
        recent = add_derived_features(pd.read_parquet(labeled[-1]))
        p_recent = chal.predict_proba(tr.to_model_frame(recent, num, cat))[:, 1]
        thr, _ = tr.cost_optimal_threshold(
            recent["target"].astype(int).to_numpy(), p_recent, recent["credit_amount"].to_numpy(float), lgd, margin
        )

        ho = add_derived_features(pd.read_parquet(holdout))
        prof = json.loads((cfg.paths.production / "scenario.json").read_text(encoding="utf-8"))["reference_profile"]
        Xh, yh, ch = tr.to_model_frame(ho, num, cat), ho["target"].astype(int).to_numpy(), ho["credit_amount"].to_numpy(float)
        pc, pn = champion.predict_proba(Xh)[:, 1], chal.predict_proba(Xh)[:, 1]
        # Segmento crítico definido por FEATURES observáveis (limiares da referência), não pelo gabarito
        seg = ((ho["debt_to_income"] >= prof["dti_threshold"]) & (ho["income"] <= prof["income_threshold"])).to_numpy()
        aud = pd.read_parquet(cfg.paths.audit / holdout.name).set_index("customer_id").loc[ho["customer_id"]].reset_index(drop=True)
        aud["age_band"] = age_band(ho["age_years"].reset_index(drop=True))
        min_size = cfg.slo["model"]["min_group_size"]
        from sklearn.metrics import roc_auc_score

        # Limiar recalibrado do champion com a janela rotulada mais recente (resposta "moderada")
        p_recent_c = champion.predict_proba(tr.to_model_frame(recent, num, cat))[:, 1]
        thr_recal, _ = tr.cost_optimal_threshold(
            recent["target"].astype(int).to_numpy(), p_recent_c, recent["credit_amount"].to_numpy(float), lgd, margin
        )

        def option(name: str, scores: np.ndarray, approve: np.ndarray, kind: str) -> dict[str, Any]:
            # avalia a política de aprovação: aprovação = PD abaixo do limiar efetivo (codificado em 'approve')
            pseudo = np.where(approve, 0.0, 1.0)
            m = tr.evaluate(yh, pseudo, ch, 0.5, lgd, margin)
            m.update({k: v for k, v in tr.evaluate(yh, scores, ch, 1.1, lgd, margin).items() if k in ("auc", "ks", "brier")})
            m["expected_loss"] = round(float((scores[approve] * ch[approve] * lgd).sum()), 2)
            fs = fairness.fairness_summary(yh, pseudo, 0.5, aud[["gender", "age_band"]], min_size)
            prot = fs[fs["attribute"].isin(cfg.slo["model"]["fairness_protected_attributes"])]
            mon = fs[fs["attribute"].isin(cfg.slo["model"]["fairness_monitored_attributes"])]
            return {
                "opcao": name,
                "tipo": kind,
                **m,
                "auc_segmento": round(float(roc_auc_score(yh[seg], scores[seg])), 4),
                "min_disparate_impact": round(float(prot["disparate_impact"].min()), 4),
                "di_faixa_etaria_monitorada": round(float(mon["disparate_impact"].min()), 4) if len(mon) else None,
            }

        rule_thr = round(meta["threshold"] / 2, 2)
        options = [
            option("1. Nenhuma ação (champion)", pc, pc < meta["threshold"], "baseline"),
            option(f"2. Recalibrar limiar ({meta['threshold']} → {thr_recal})", pc, pc < thr_recal, "moderada"),
            option(
                f"3. Regra temporária no segmento (PD < {rule_thr})",
                pc,
                np.where(seg, pc < rule_thr, pc < meta["threshold"]),
                "severa-imediata",
            ),
            option(f"4. Retreino challenger (limiar {thr})", pn, pn < thr, "severa-estrutural"),
        ]
        opt_df = pd.DataFrame(options)
        opt_df.to_csv(cfg.paths.reports / "model" / "response_options.csv", index=False)
        base_opt, chal_opt = options[0], options[3]
        guards = {
            "auc_global_melhor": chal_opt["auc"] > base_opt["auc"],
            "segmento_critico_nao_pior": chal_opt["auc_segmento"] >= base_opt["auc_segmento"] - 0.005,
            "fairness_regra_4_5": chal_opt["min_disparate_impact"] >= cfg.slo["model"]["fairness_min_disparate_impact"],
            "calibracao_brier_melhor": chal_opt["brier"] <= base_opt["brier"],
            "custo_esperado_menor": chal_opt["expected_cost"] < base_opt["expected_cost"],
        }
        promoted = all(guards.values())
        fair_ok = opt_df["min_disparate_impact"] >= cfg.slo["model"]["fairness_min_disparate_impact"]
        recommended = opt_df[fair_ok].sort_values("expected_cost").iloc[0]["opcao"] if fair_ok.any() else options[0]["opcao"]
        if promoted:
            recommended = chal_opt["opcao"]
        import mlflow
        from mlflow.models import infer_signature

        with tracking.run(tracking.EXP_TRAINING, "xgboost-challenger", tags={"stage": "retrain", "model.role": "challenger"}):
            tracking.log_dict_flat("holdout_champion_", tr.evaluate(yh, pc, ch, meta["threshold"], lgd, margin))
            tracking.log_dict_flat("holdout_challenger_", tr.evaluate(yh, pn, ch, thr, lgd, margin))
            mlflow.log_metrics(
                {
                    "segment_auc_champion": base_opt["auc_segmento"],
                    "segment_auc_challenger": chal_opt["auc_segmento"],
                    "challenger_min_di": chal_opt["min_disparate_impact"],
                }
            )
            mlflow.log_dict({k: bool(v) for k, v in guards.items()}, "guardrails.json")
            mlflow.log_dict(json.loads(opt_df.to_json(orient="records")), "response_options.json")
            info = tracking.log_sklearn_model(chal, infer_signature(Xh.head(200), pn[:200]), Xh.head(5), tracking.REGISTERED_MODEL)
        client = MlflowClient()
        client.set_registered_model_alias(tracking.REGISTERED_MODEL, "challenger", info.registered_model_version)
        if promoted:
            client.set_registered_model_alias(tracking.REGISTERED_MODEL, "champion", info.registered_model_version)
            meta_n = {**meta, "threshold": thr, "model_version": info.registered_model_version, "promoted_from_challenger": True}
            tr.save_bundle(cfg.paths.models / "challenger_promoted", chal, meta_n)
        result = {
            "holdout_window": holdout.stem,
            "champion": tr.evaluate(yh, pc, ch, meta["threshold"], lgd, margin),
            "challenger": tr.evaluate(yh, pn, ch, thr, lgd, margin),
            "segment_auc_champion": base_opt["auc_segmento"],
            "segment_auc_challenger": chal_opt["auc_segmento"],
            "challenger_threshold": thr,
            "challenger_min_disparate_impact": chal_opt["min_disparate_impact"],
            "guardrails": guards,
            "promoted": promoted,
            "challenger_version": info.registered_model_version,
            "trained_on": [p.stem for p in labeled],
            "response_options": options,
            "recommended_response": recommended,
        }
        _write_json(cfg.paths.reports / "model" / "challenger_evaluation.json", result)
        log_event("CHALLENGER_EVALUATED", promoted=promoted, guardrails=guards, recommended=recommended)
    return result


# ----------------------------------------------------------------------------- causalidade
def causal() -> dict[str, Any]:
    cfg = get_settings()
    num, cat = cfg.numeric_features, cfg.categorical_features
    pipe, meta = tr.load_bundle(cfg.paths.models / "champion")
    ref = pd.read_parquet(cfg.paths.reference / "monitoring_reference.parquet")
    scen = json.loads((cfg.paths.production / "scenario.json").read_text(encoding="utf-8"))
    drift = pd.read_csv(cfg.paths.reports / "drift" / "drift_summary.csv")
    with stage("causal") as ctx:
        per_window = []
        for w in scen["windows"]:
            win = pd.read_parquet(cfg.paths.production / f"{w['window']}.parquet")
            gt = pd.read_parquet(cfg.paths.production / "_ground_truth" / f"{w['window']}.parquet")
            d = attribution.decompose(pipe, win, gt, num, cat, meta["baseline_test"]["auc"], meta["baseline_test"]["default_rate"])
            per_window.append({"window": w["window"], "month": w["month"], **d})
        last = scen["windows"][-1]["window"]
        win = pd.read_parquet(cfg.paths.production / f"{last}.parquet")
        gt = pd.read_parquet(cfg.paths.production / "_ground_truth" / f"{last}.parquet")
        abl = attribution.feature_ablation(pipe, win, ref, num, cat, meta["threshold"])
        seg = attribution.segment_effect(pipe, win, gt, num, cat)
        ctx["rows"] = len(win)
        injected = {
            "income": "covariate (reamostragem)",
            "debt_to_income": "covariate (reamostragem)",
            "age_years": "covariate (reamostragem)",
            "credit_to_income": "multivariado (desacoplamento)",
            "children_count": "controle (não alterada)",
            "region_rating": "controle (não alterada)",
        }
        last_drift = drift[drift["window"] == last].set_index("feature")
        validation = [
            {
                "feature": f,
                "mecanismo_injetado": m,
                "psi": float(last_drift.loc[f, "psi"]),
                "drift_detectado": bool(last_drift.loc[f, "drift_detected"]),
            }
            for f, m in injected.items()
        ]
        out = {
            "per_window": per_window,
            "ablation_last_window": abl.to_dict(orient="records"),
            "ablation_totals": abl.attrs,
            "segment_effect_last_window": seg,
            "detection_vs_injection": validation,
        }
        _write_json(cfg.paths.reports / "causal" / "attribution.json", out)
        _write_causal_md(out, scen, meta)
        log_event(
            "CAUSAL_ANALYSIS_DONE",
            concept_effect_auc=per_window[-1]["efeito_concept_drift_auc"],
            covariate_effect_auc=per_window[-1]["efeito_covariate_shift_auc"],
        )
    return out


def _write_causal_md(out: dict, scen: dict, meta: dict) -> None:
    cfg = get_settings()
    pw = pd.DataFrame(out["per_window"])
    abl = pd.DataFrame(out["ablation_last_window"])
    val = pd.DataFrame(out["detection_vs_injection"])
    seg = out["segment_effect_last_window"]
    last = out["per_window"][-1]
    md = f"""# Análise Causal da Degradação do Modelo

> Gerado pelo estágio `causal`. Dados: 6 janelas simuladas (ver `configs/drift_scenario.yaml`).

## 1. Hipótese causal (DAG)

```mermaid
flowchart LR
    INF[Inflação / juros altos] --> RR[Renda real ↓]
    INF --> PARC[Parcelas ↑]
    RR --> DTI[Comprometimento de renda ↑]
    PARC --> DTI
    DIG[Novo canal digital] --> JOV[Público mais jovem]
    DIG --> LIM[Limites automáticos<br/>crédito desacoplado da renda]
    DTI --> DEF[Inadimplência ↑]
    LIM --> DEF
    DTI -. segmento vulnerável .-> CON[Mesmo perfil, risco maior<br/>= concept drift]
    CON --> DEF
```

## 2. Decomposição covariate shift × concept drift (por janela)

O modelo é avaliado em cada janela com o rótulo real e com o rótulo **contrafactual** (comportamento
que os mesmos clientes teriam sem a mudança de risco). A diferença isola o efeito do concept drift.

{pw[["month", "auc_baseline", "auc_contrafactual_sem_concept", "auc_real", "efeito_covariate_shift_auc", "efeito_concept_drift_auc", "default_rate_real", "pd_media_prevista", "subestimacao_risco_pp"]].to_markdown(index=False)}

**Leitura (última janela, {last["month"]}):** a AUC foi de {last["auc_baseline"]} (referência) para {last["auc_real"]}.
A mudança de população explica {last["efeito_covariate_shift_auc"]:+.4f} e o concept drift explica
{last["efeito_concept_drift_auc"]:+.4f}. O modelo prevê PD média de {last["pd_media_prevista"]:.2%}, mas a
inadimplência real foi {last["default_rate_real"]:.2%}: **risco subestimado em {last["subestimacao_risco_pp"]} p.p.**

## 3. Onde o conceito mudou (segmento vulnerável, última janela)

| Grupo | n | Default real | Default sem concept drift | PD prevista |
|---|---|---|---|---|
| Segmento vulnerável (DTI alto + renda baixa) | {seg["segmento_vulneravel"]["n"]} | {seg["segmento_vulneravel"]["default_real"]:.2%} | {seg["segmento_vulneravel"]["default_sem_concept"]:.2%} | {seg["segmento_vulneravel"]["pd_prevista"]:.2%} |
| Demais clientes | {seg["demais_clientes"]["n"]} | {seg["demais_clientes"]["default_real"]:.2%} | {seg["demais_clientes"]["default_sem_concept"]:.2%} | {seg["demais_clientes"]["pd_prevista"]:.2%} |

## 4. Ablação contrafactual por feature (última janela)

Cada feature é devolvida à distribuição da referência (*quantile mapping*); o quanto a PD média
prevista e a taxa de aprovação "voltam" mede a contribuição de cada mudança de distribuição.
Variação total vs. referência: PD média {out["ablation_totals"].get("total_delta_pd_pp")} p.p., aprovação {out["ablation_totals"].get("total_delta_aprovacao_pp")} p.p.

{abl.to_markdown(index=False)}

## 5. Validação: detectado × injetado

{val.to_markdown(index=False)}

## 6. Conclusão e prescrição

1. **Causa raiz:** a degradação de desempenho é dominada pelo **concept drift** no segmento de alto
   comprometimento de renda e renda baixa. A mudança de população (renda menor, parcelas maiores,
   público jovem) desloca as features, mas o modelo ainda ordena bem esses clientes.
2. **Impacto de negócio:** o modelo **subestima o risco** (PD prevista < inadimplência real), o que
   aprova crédito demais no segmento vulnerável e eleva a perda realizada acima da esperada.
3. **Ações (política em camadas — ADR-0006):**
   - imediato: regra de negócio temporária para o segmento (revisão manual / limite reduzido);
   - curto prazo: recalibrar o limiar de corte por custo esperado;
   - estrutural: retreino com as janelas rotuladas mais recentes, promovido somente se passar nos
     guardrails (global, segmento, fairness, calibração) — ver `reports/model/challenger_evaluation.json`.
"""
    path = cfg.paths.reports / "causal" / "attribution.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")


# ----------------------------------------------------------------------------- governança
def scan_pii() -> list[dict]:
    cfg = get_settings()
    with stage("pii_scan") as ctx:
        files = [p for d in (cfg.paths.reports, cfg.paths.logs) for p in d.rglob("*") if p.is_file()]
        forbidden = {cfg.pseudonym_salt} if cfg.pseudonym_salt else set()
        source = cfg.paths.root / cfg.dataset["source_file"]
        raw_ids: set[str] = set()
        if source.exists():
            raw_ids = set(pd.read_csv(source, usecols=["SK_ID_CURR"], nrows=500)["SK_ID_CURR"].astype(str))
        findings = scan_files(files, forbidden)
        # IDs originais não podem aparecer em relatórios/logs (busca por token exato em CSV/JSON)
        for p in files:
            if p.suffix in {".csv", ".json", ".jsonl", ".md"}:
                text = p.read_text(encoding="utf-8", errors="ignore")
                hits = [i for i in list(raw_ids)[:500] if f'"{i}"' in text or f",{i}," in text]
                if hits:
                    findings.append({"file": p.as_posix(), "type": "raw_customer_id"})
        ctx["rows"] = len(files)
        _write_json(cfg.paths.reports / "governance" / "pii_scan.json", {"files_scanned": len(files), "findings": findings})
        log_event(
            "PII_SCAN_DONE",
            files=len(files),
            findings=len(findings),
            status="success" if not findings else "failed",
            level="INFO" if not findings else "CRITICAL",
        )
    return findings


def retention(dry_run: bool = True) -> list[dict]:
    cfg = get_settings()
    with stage("retention"):
        actions = apply_retention(cfg.paths.root, dry_run=dry_run)
        _write_json(cfg.paths.reports / "governance" / "retention_plan.json", {"dry_run": dry_run, "actions": actions})
    return actions


def bad_batch_demo() -> dict[str, Any]:
    """Teste de fumaça do contrato: o lote defeituoso precisa ser BLOQUEADO (exit code 2)."""
    try:
        validate_batch(bad=True)
    except IngestionBlocked as exc:
        log_event("BAD_BATCH_BLOCKED_AS_EXPECTED", gate=exc.gate, reason=exc.reason, exit_code=exc.code)
        return {"blocked": True, "reason": exc.reason}
    raise RuntimeError("O lote defeituoso NÃO foi bloqueado — o contrato está falhando!")


def _schema_for_docs() -> str:
    cfg = get_settings()
    return str(build_schema(load_contract(cfg.contract_path)))
