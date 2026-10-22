import numpy as np
import pandas as pd

from driftguard.core.data.canonical import add_derived_features, canonicalize
from driftguard.core.data.leakage import leakage_report
from driftguard.core.data.split import split_reference_production
from driftguard.core.data.synthetic import synthetic_applications
from driftguard.core.drift import stats
from driftguard.core.drift.simulate import simulate_windows
from driftguard.core.privacy.pseudonymize import apply_privacy
from driftguard.settings import get_settings

NUM = ["income", "credit_amount", "age_years"]
CAT = ["contract_type"]


def _data(n=20_000):
    df = canonicalize(synthetic_applications(n))
    return apply_privacy(df, "customer_id", ["gender", "family_status"], "s")


def test_reference_and_production_are_disjoint():
    model_tbl, _ = _data(6000)
    ref, prod = split_reference_production(model_tbl, "target", 0.4, 0)
    rep = leakage_report(ref, prod, "customer_id", NUM, CAT)
    assert rep["passed"] and rep["id_overlap"] == 0


def test_leakage_report_catches_duplicates():
    model_tbl, _ = _data(3000)
    ref = model_tbl.iloc[:2000]
    prod = pd.concat([model_tbl.iloc[2000:], model_tbl.iloc[:5]])
    rep = leakage_report(ref, prod, "customer_id", NUM, CAT)
    assert not rep["passed"] and rep["id_overlap"] == 5 and rep["exact_duplicate_rows"] >= 5


def test_simulation_shifts_target_features_and_keeps_controls(tmp_path):
    cfg = get_settings()
    model_tbl, audit = _data(20_000)
    ref, pool = split_reference_production(model_tbl, "target", 0.5, 0)
    manifest = simulate_windows(pool, audit[audit["customer_id"].isin(pool["customer_id"])], ref, cfg.scenario, tmp_path / "production")
    last = pd.read_parquet(tmp_path / "production" / f"{manifest[-1]['window']}.parquet")
    r, c = add_derived_features(ref), add_derived_features(last)
    assert stats.psi(r["income"], c["income"]) > 0.10
    assert stats.psi(r["debt_to_income"], c["debt_to_income"]) > 0.10
    assert stats.psi(r["children_count"], c["children_count"]) < 0.10
    gt = pd.read_parquet(tmp_path / "production" / "_ground_truth" / f"{manifest[-1]['window']}.parquet")
    assert not gt.loc[gt["flipped"], "in_segment"].eq(False).any()  # concept drift só no segmento
    assert (last["target"].to_numpy() >= gt["target_original"].to_numpy()).all()
    assert manifest[-1]["default_rate"] > manifest[0]["default_rate"]
    assert np.all(np.diff([w["intensity"] for w in manifest]) > 0)
