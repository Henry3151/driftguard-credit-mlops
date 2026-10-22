"""Quality gates com circuit breaker, quarentena (dead-letter) e Data Quality Score.

Política (ADR-0004):
- Violação de schema/volume OU fração de linhas inválidas > limite  -> LOTE BLOQUEADO.
- Fração <= limite -> apenas as linhas inválidas vão para a quarentena com reason_code.
Toda decisão gera log estruturado, métrica Prometheus e relatório JSON.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors

from driftguard.core.contracts.loader import Contract, build_schema
from driftguard.core.observability import metrics
from driftguard.core.observability.logging import RUN, log_event

EXIT_BLOCKED = 2


class IngestionBlocked(SystemExit):
    """Lote barrado pelo contrato: encerra o processo com exit code 2."""

    def __init__(self, gate: str, reason: str):
        self.gate, self.reason = gate, reason
        super().__init__(EXIT_BLOCKED)


@dataclass
class GateResult:
    gate: str
    batch: str
    contract: str
    n_rows: int
    n_invalid_rows: int
    invalid_fraction: float
    decision: str  # accepted | accepted_with_quarantine | blocked
    blocked_reason: str | None
    rule_failures: dict[str, int]  # regra -> nº de ocorrências
    warnings: list[dict[str, Any]] = field(default_factory=list)
    dq_score: float = 100.0
    dq_dimensions: dict[str, float] = field(default_factory=dict)
    quarantine_path: str | None = None

    @property
    def blocked(self) -> bool:
        return self.decision == "blocked"


def _relative(path: Path) -> str:
    """Caminhos relativos à raiz do projeto (relatórios portáveis, sem expor o ambiente)."""
    from driftguard.settings import get_settings

    try:
        return path.resolve().relative_to(get_settings().paths.root.resolve()).as_posix()
    except ValueError:
        return path.name


def _rule_name(row: pd.Series) -> str:
    col = row.get("column")
    check = str(row.get("check"))
    if col is None or (isinstance(col, float) and np.isnan(col)):
        return f"schema::{check}"
    return f"{col}::{check}"


def _schema_level(fc: pd.DataFrame) -> pd.DataFrame:
    """Falhas sem índice de linha (coluna ausente/extra, coerção total) = estruturais."""
    return fc[fc["index"].isna()]


def data_quality_score(
    df: pd.DataFrame, contract: Contract, invalid_rows: set, freshness_ok: bool = True
) -> tuple[float, dict[str, float]]:
    """Score 0-100 pelas 6 dimensões de qualidade (Zhou et al., 2024)."""
    n = max(len(df), 1)
    req = [c for c in contract.required_fields if c in df.columns]
    opt = [c for c in contract.optional_fields if c in df.columns and c != "target"]
    completeness = 1 - (0.8 * df[req].isna().mean().mean() if req else 0) - (0.2 * df[opt].isna().mean().mean() if opt else 0)
    uniqueness = 1 - (df["customer_id"].duplicated().sum() / n if "customer_id" in df else 0)
    validity = 1 - len(invalid_rows) / n
    consistency = 1.0
    if {"annuity", "credit_amount"} <= set(df.columns):
        a = pd.to_numeric(df["annuity"], errors="coerce")
        c = pd.to_numeric(df["credit_amount"], errors="coerce")
        consistency = 1 - float((a > c).mean())
    accuracy = 1.0
    if "income" in df:
        x = pd.to_numeric(df["income"], errors="coerce")
        med = x.median()
        mad = (x - med).abs().median() or 1.0
        accuracy = 1 - float(((x - med).abs() / (1.4826 * mad) > 25).mean())
    dims = {
        "completude": completeness,
        "unicidade": uniqueness,
        "validade": validity,
        "consistencia": consistency,
        "acuracia": accuracy,
        "atualidade": 1.0 if freshness_ok else 0.0,
    }
    dims = {k: round(max(0.0, min(1.0, float(v))) * 100, 2) for k, v in dims.items()}
    weights = {"completude": 0.2, "unicidade": 0.15, "validade": 0.3, "consistencia": 0.15, "acuracia": 0.1, "atualidade": 0.1}
    return round(sum(dims[k] * w for k, w in weights.items()), 2), dims


def _warnings(df: pd.DataFrame, contract: Contract, slo: dict, reference_stats: dict | None) -> list[dict]:
    out: list[dict] = []
    q = slo["quality"]
    for c in contract.optional_fields:
        if c in df.columns and c not in {"target", "gender", "family_status"}:
            rate = float(df[c].isna().mean())
            if rate > q["warning_max_null_fraction_optional"]:
                out.append({"rule": "optional_null_rate", "column": c, "value": round(rate, 4)})
    if {"annuity", "credit_amount"} <= set(df.columns):
        bad = int((pd.to_numeric(df["annuity"], errors="coerce") > pd.to_numeric(df["credit_amount"], errors="coerce")).sum())
        if bad:
            out.append({"rule": "annuity_le_credit", "rows": bad})
    if reference_stats and "income" in df.columns:
        mean = float(pd.to_numeric(df["income"], errors="coerce").mean())
        ref = reference_stats["income_mean"]
        shift = (mean - ref) / ref
        if abs(shift) > q["warning_income_mean_tolerance"]:
            out.append({"rule": "income_mean_shift", "value": round(shift, 4)})
    return out


def run_gate(
    df: pd.DataFrame,
    contract: Contract,
    slo: dict,
    gate: str,
    batch_name: str,
    quarantine_dir: Path,
    report_dir: Path,
    *,
    require_target: bool = False,
    reference_stats: dict | None = None,
    schema: pa.DataFrameSchema | None = None,
    raise_on_block: bool = True,
) -> tuple[pd.DataFrame, GateResult]:
    """Valida um lote; devolve somente as linhas aprovadas (já com tipos coercidos)."""
    schema = schema or build_schema(contract, require_target=require_target)
    q = slo["quality"]
    n = len(df)
    structural: list[str] = []
    rule_failures: dict[str, int] = {}
    invalid_idx: set = set()
    failure_cases = pd.DataFrame()

    if n < q["min_batch_rows"]:
        structural.append("min_batch_rows")
        rule_failures["batch::min_batch_rows"] = 1

    try:
        validated = schema.validate(df, lazy=True)
    except SchemaErrors as err:
        failure_cases = err.failure_cases.copy()
        for name, cnt in failure_cases.apply(_rule_name, axis=1).value_counts().items():
            rule_failures[name] = int(cnt)
        struct_fc = _schema_level(failure_cases)
        if len(struct_fc):
            structural.extend(sorted(set(struct_fc.apply(_rule_name, axis=1))))
        invalid_idx = set(failure_cases["index"].dropna().astype(int).tolist())
        validated = None

    invalid_fraction = len(invalid_idx) / max(n, 1)
    reasons = []
    if structural:
        reasons.append("violação estrutural: " + ", ".join(structural[:5]))
    if invalid_fraction > q["circuit_breaker_max_invalid_fraction"]:
        reasons.append(f"circuit breaker: {invalid_fraction:.1%} de linhas inválidas > {q['circuit_breaker_max_invalid_fraction']:.0%}")
    blocked_reason = " | ".join(reasons) or None

    dq, dims = data_quality_score(df, contract, invalid_idx)
    warnings = _warnings(df, contract, slo, reference_stats)
    decision = "blocked" if blocked_reason else ("accepted_with_quarantine" if invalid_idx else "accepted")

    quarantine_path = None
    if invalid_idx or blocked_reason:
        qdir = quarantine_dir / f"{RUN.run_id}_{gate}_{batch_name}"
        qdir.mkdir(parents=True, exist_ok=True)
        bad_rows = df if blocked_reason else df.loc[sorted(invalid_idx)]
        bad_rows.to_csv(qdir / "rows.csv", index=True, index_label="row_index")
        if len(failure_cases):
            fc = failure_cases.assign(reason_code=failure_cases.apply(_rule_name, axis=1))
            fc[["index", "column", "check", "failure_case", "reason_code"]].to_csv(qdir / "failure_cases.csv", index=False)
        quarantine_path = _relative(qdir)

    result = GateResult(
        gate=gate,
        batch=batch_name,
        contract=f"{contract.id}@{contract.version}",
        n_rows=n,
        n_invalid_rows=len(invalid_idx),
        invalid_fraction=round(invalid_fraction, 6),
        decision=decision,
        blocked_reason=blocked_reason,
        rule_failures=rule_failures,
        warnings=warnings,
        dq_score=dq,
        dq_dimensions=dims,
        quarantine_path=quarantine_path,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{gate}_{batch_name}.json").write_text(json.dumps(asdict(result), indent=2, ensure_ascii=False), encoding="utf-8")

    # --- telemetria ---------------------------------------------------------
    metrics.DQ_SCORE.labels(batch_name).set(dq)
    for rule, cnt in rule_failures.items():
        metrics.ROWS_REJECTED.labels(rule.split("::")[0] + "::" + rule.split("::")[-1][:40]).inc(cnt)
    for w in warnings:
        log_event("DATA_QUALITY_WARNING", level="WARNING", gate=gate, batch=batch_name, **w)

    if blocked_reason:
        metrics.BATCHES_BLOCKED.labels(gate).inc()
        log_event(
            "INGESTION_BLOCKED",
            level="CRITICAL",
            status="blocked",
            gate=gate,
            batch=batch_name,
            rows=n,
            invalid_rows=len(invalid_idx),
            reason=blocked_reason,
            top_rules=dict(sorted(rule_failures.items(), key=lambda kv: -kv[1])[:8]),
            quarantine=quarantine_path,
        )
        if raise_on_block:
            raise IngestionBlocked(gate, blocked_reason)
        return df.iloc[0:0], result

    if invalid_idx:
        log_event(
            "ROWS_QUARANTINED",
            level="WARNING",
            status="partial",
            gate=gate,
            batch=batch_name,
            rows=n,
            invalid_rows=len(invalid_idx),
            rules=rule_failures,
            quarantine=quarantine_path,
        )
        clean = df.drop(index=list(invalid_idx))
        validated = schema.validate(clean, lazy=True)
    log_event("GATE_PASSED", status="success", gate=gate, batch=batch_name, rows=len(validated), dq_score=dq)
    return validated, result
