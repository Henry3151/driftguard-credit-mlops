import pandas as pd

from driftguard.core.drift.policy import decide
from driftguard.core.observability.alerts import Alert, AlertManager, read_alerts


def test_alert_deduplication_and_routing(tmp_path):
    am = AlertManager(tmp_path / "alerts.jsonl")
    a = Alert("DATA_DRIFT", "warning", "Drift severo em income", "window_01")
    assert am.fire(a, dedup_key="income") and not am.fire(a, dedup_key="income")
    rows = read_alerts(tmp_path / "alerts.jsonl")
    assert len(rows) == 1 and rows[0]["priority"] == "P2" and rows[0]["runbook"].endswith("#data-drift")


def _table(severity="severo", important=True, drift=True):
    return pd.DataFrame([{"feature": "income", "severity": severity, "important": important, "drift_detected": drift}])


def test_policy_composite_alert_is_severe(slo):
    base = {"auc": 0.74, "brier": 0.07}
    d = decide(_table(), {"auc": 0.68, "brier": 0.08}, base, slo, mmd_significant=True)
    assert d["level"] == "severo" and d["performance_breach"]


def test_policy_drift_without_impact_is_not_severe(slo):
    base = {"auc": 0.74, "brier": 0.07}
    d = decide(_table("moderado", important=False), {"auc": 0.739, "brier": 0.07}, base, slo, mmd_significant=False)
    assert d["level"] in {"leve", "moderado"} and not d["performance_breach"]


def test_policy_stable(slo):
    d = decide(_table("estavel", drift=False), {"auc": 0.74, "brier": 0.07}, {"auc": 0.74, "brier": 0.07}, slo, False)
    assert d["level"] == "estavel"
