import numpy as np
import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from driftguard.core.drift import stats


def test_psi_identical_distribution_is_near_zero():
    x = pd.Series(np.random.default_rng(0).normal(size=20_000))
    assert stats.psi(x, x.sample(frac=1, random_state=1)) < 1e-3


def test_psi_detects_large_shift():
    rng = np.random.default_rng(1)
    ref, cur = pd.Series(rng.normal(0, 1, 10_000)), pd.Series(rng.normal(1, 1, 10_000))
    assert stats.psi(ref, cur) >= 0.25


def test_psi_known_categorical_value():
    # (0.1-0.2)ln(0.1/0.2) + (0.4-0.5)ln(0.4/0.5) + (0.5-0.3)ln(0.5/0.3) = 0.0693 + 0.0223 + 0.1022 ≈ 0.1938
    ref = pd.Series(["a"] * 20 + ["b"] * 50 + ["c"] * 30)
    cur = pd.Series(["a"] * 10 + ["b"] * 40 + ["c"] * 50)
    assert abs(stats.psi(ref, cur) - 0.1938) < 1e-3


def test_category_normalization_int_vs_float():
    ref = pd.Series([1, 2, 3] * 100, dtype="Int64")
    cur = pd.Series([1.0, 2.0, 3.0] * 100)
    assert stats.psi(ref, cur) < 1e-6


@settings(max_examples=40, deadline=None)
@given(
    st.lists(st.floats(-1e3, 1e3, allow_nan=False), min_size=30, max_size=300),
    st.lists(st.floats(-1e3, 1e3, allow_nan=False), min_size=30, max_size=300),
)
def test_psi_is_non_negative(a, b):
    assert stats.psi(pd.Series(a), pd.Series(b)) >= -1e-9


def test_benjamini_hochberg():
    flags = stats.benjamini_hochberg([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205], alpha=0.05)
    assert flags[:2] == [True, True] and not any(flags[5:])


def test_ks_detects_shift_and_effect_size():
    rng = np.random.default_rng(2)
    d, p = stats.ks_test(pd.Series(rng.normal(0, 1, 5000)), pd.Series(rng.normal(0.5, 1, 5000)))
    assert p < 1e-6 and d > 0.15


def test_mmd_detects_correlation_inversion_with_same_marginals():
    rng = np.random.default_rng(3)
    a = rng.normal(size=600)
    X = np.c_[a, a + 0.3 * rng.normal(size=600)]
    b = rng.normal(size=600)
    Y = np.c_[b, -b + 0.3 * rng.normal(size=600)]
    _, p_drift = stats.mmd_permutation_test(X, Y, n_perm=100)
    c = rng.normal(size=600)
    Z = np.c_[c, c + 0.3 * rng.normal(size=600)]
    _, p_same = stats.mmd_permutation_test(X, Z, n_perm=100)
    assert p_drift < 0.05 < p_same


def test_feature_drift_table_flags_only_shifted_feature(slo):
    rng = np.random.default_rng(4)
    ref = pd.DataFrame({"x": rng.normal(0, 1, 8000), "y": rng.normal(0, 1, 8000), "c": rng.choice(["a", "b"], 8000)})
    cur = pd.DataFrame({"x": rng.normal(0.8, 1, 8000), "y": rng.normal(0, 1, 8000), "c": rng.choice(["a", "b"], 8000)})
    t = stats.feature_drift_table(ref, cur, ["x", "y"], ["c"], slo, {"x": 0.5, "y": 0.3, "c": 0.2}).set_index("feature")
    assert t.loc["x", "drift_detected"] and not t.loc["y", "drift_detected"] and not t.loc["c", "drift_detected"]


def test_adwin_finds_change_point():
    stream = np.r_[np.full(3000, 0.1), np.full(3000, 0.6)] + np.random.default_rng(5).normal(0, 0.05, 6000)
    cps = stats.adwin_change_points(stream)
    assert cps and 3000 <= cps[0] < 3500
