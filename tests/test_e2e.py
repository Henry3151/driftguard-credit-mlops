"""Teste ponta a ponta com dados SINTÉTICOS (o dataset real não é redistribuível)."""

import json
import os
import subprocess
import sys

import pandas as pd
import pytest


@pytest.mark.e2e
def test_demo_pipeline_end_to_end(isolated_root):
    env = {**os.environ, "DRIFTGUARD_ROOT": str(isolated_root)}
    proc = subprocess.run(
        [sys.executable, "-m", "driftguard", "demo", "--rows", "24000"], env=env, capture_output=True, text=True, timeout=900
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    rep = isolated_root / "reports"
    assert (rep / "dashboard" / "index.html").stat().st_size > 100_000
    assert len(list((rep / "drift").glob("evidently_window_*.html"))) == 6
    bad = json.loads((rep / "validation" / "gate1_contract_bad_batch.json").read_text(encoding="utf-8"))
    assert bad["decision"] == "blocked"
    drift = pd.read_csv(rep / "drift" / "drift_summary.csv")
    last = drift[drift["window"] == drift["window"].max()].set_index("feature")
    assert last.loc["income", "drift_detected"] and last.loc["debt_to_income", "drift_detected"]
    assert not last.loc["children_count", "drift_detected"]
    wm = pd.read_csv(rep / "monitoring" / "window_metrics.csv")
    # o risco real cresce e o modelo passa a subestimá-lo (perda realizada > esperada)
    assert wm["default_rate"].iloc[-1] > wm["default_rate"].iloc[0]
    assert wm["loss_gap"].iloc[-1] > wm["loss_gap"].iloc[0]
    assert json.loads((rep / "governance" / "pii_scan.json").read_text(encoding="utf-8"))["findings"] == []
    assert (rep / "alerts.jsonl").read_text(encoding="utf-8").count("DATA_DRIFT") >= 1
