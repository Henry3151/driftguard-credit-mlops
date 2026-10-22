import pandas as pd

from driftguard.core.privacy.pii_scan import scan_files
from driftguard.core.privacy.pseudonymize import age_band, apply_privacy, pseudonymize_id


def test_pseudonymization_is_deterministic_and_salt_dependent():
    assert pseudonymize_id("100002", "s1") == pseudonymize_id("100002", "s1")
    assert pseudonymize_id("100002", "s1") != pseudonymize_id("100002", "s2")
    assert "100002" not in pseudonymize_id("100002", "s1")


def test_sensitive_attributes_are_segregated():
    df = pd.DataFrame({"customer_id": ["1", "2"], "income": [1.0, 2.0], "gender": ["F", "M"], "family_status": ["Married", "Widow"]})
    model_tbl, audit = apply_privacy(df, "customer_id", ["gender", "family_status"], "salt")
    assert "gender" not in model_tbl and "family_status" not in model_tbl
    assert list(audit.columns) == ["customer_id", "gender", "family_status"]
    assert set(model_tbl["customer_id"]) == set(audit["customer_id"]) and "1" not in set(model_tbl["customer_id"])


def test_pii_scan_flags_leaked_secret(tmp_path):
    (tmp_path / "ok.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "leak.log").write_text("salt=super-secret", encoding="utf-8")
    findings = scan_files(list(tmp_path.iterdir()), {"super-secret"})
    assert [f["file"].endswith("leak.log") for f in findings] == [True]


def test_age_band_generalization():
    assert age_band(pd.Series([19, 30, 70])).tolist() == ["18-25", "26-35", "65+"]
