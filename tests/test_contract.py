import pandas as pd
import pytest

from driftguard.core.contracts.gates import IngestionBlocked, run_gate
from driftguard.core.contracts.loader import load_contract
from driftguard.core.data.canonical import canonicalize
from driftguard.core.data.synthetic import bad_batch, synthetic_applications
from driftguard.settings import get_settings


@pytest.fixture()
def ctx(tmp_path):
    cfg = get_settings()
    return load_contract(cfg.contract_path), cfg.slo, tmp_path


def test_contract_has_at_least_three_blocker_rules(ctx):
    contract, _, _ = ctx
    assert contract.rule_count()["blocker"] >= 3


def test_clean_batch_is_accepted(ctx):
    contract, slo, tmp = ctx
    ok, res = run_gate(canonicalize(synthetic_applications(3000)), contract, slo, "g1", "clean", tmp / "q", tmp / "r", require_target=True)
    assert res.decision == "accepted" and len(ok) == 3000 and res.dq_score > 95


def test_bad_batch_is_blocked_and_every_injected_error_is_caught(ctx):
    contract, slo, tmp = ctx
    df, injected = bad_batch()
    with pytest.raises(IngestionBlocked) as exc:
        run_gate(canonicalize(df), contract, slo, "g1", "bad", tmp / "q", tmp / "r")
    assert exc.value.code == 2
    import json

    report = json.loads((tmp / "r" / "g1_bad.json").read_text(encoding="utf-8"))
    rules = " ".join(report["rule_failures"])
    for expected in (
        "income::not_nullable",
        "age_years::greater_than_or_equal_to(18)",
        "income::greater_than(0)",
        "contract_type::isin",
        "customer_id::field_uniqueness",
        "ext_source_2::less_than_or_equal_to(1)",
        "column_in_schema",
    ):
        assert expected in rules, expected
    assert sum(injected.values()) > 0 and (tmp / "q").exists()


def test_small_invalid_fraction_goes_to_quarantine_not_block(ctx):
    contract, slo, tmp = ctx
    df = canonicalize(synthetic_applications(3000))
    df.loc[:9, "income"] = None  # 0,3% inválido < 5% (circuit breaker)
    ok, res = run_gate(df, contract, slo, "g1", "partial", tmp / "q", tmp / "r", require_target=True)
    assert res.decision == "accepted_with_quarantine" and res.n_invalid_rows == 10 and len(ok) == 2990
    assert pd.read_csv(next((tmp / "q").glob("*partial*/rows.csv"))).shape[0] == 10


def test_tiny_batch_is_blocked_by_volume_rule(ctx):
    contract, slo, tmp = ctx
    with pytest.raises(IngestionBlocked):
        run_gate(canonicalize(synthetic_applications(50)), contract, slo, "g1", "tiny", tmp / "q", tmp / "r")


def test_canonicalize_maps_employment_sentinel_without_imputation():
    raw = synthetic_applications(500)
    out = canonicalize(raw)
    pens = raw["DAYS_EMPLOYED"] == 365243
    assert (out.loc[pens, "has_employment"] == 0).all() and (out.loc[pens, "employment_years"] == 0).all()
    assert out["income"].isna().sum() == raw["AMT_INCOME_TOTAL"].isna().sum()
