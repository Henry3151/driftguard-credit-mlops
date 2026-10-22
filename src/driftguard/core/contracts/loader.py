"""Gera o schema Pandera a partir do contrato YAML (fonte única da verdade — ADR-0003)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandera.pandas as pa
import yaml

_DTYPES = {"string": "string", "number": "float64", "integer": "Int64"}


@dataclass(frozen=True)
class Contract:
    raw: dict[str, Any]
    model_name: str

    @property
    def version(self) -> str:
        return str(self.raw["info"]["version"])

    @property
    def id(self) -> str:
        return str(self.raw["id"])

    @property
    def fields(self) -> dict[str, dict[str, Any]]:
        return self.raw["models"][self.model_name]["fields"]

    @property
    def batch_rules(self) -> list[dict[str, Any]]:
        return self.raw["models"][self.model_name].get("quality", [])

    @property
    def required_fields(self) -> list[str]:
        return [k for k, v in self.fields.items() if v.get("required", False)]

    @property
    def optional_fields(self) -> list[str]:
        return [k for k, v in self.fields.items() if not v.get("required", False)]

    def rule_count(self) -> dict[str, int]:
        blockers = sum(
            len(field_checks(spec)) + int(spec.get("required", False)) + int(spec.get("unique", False)) for spec in self.fields.values()
        )
        blockers += sum(1 for r in self.batch_rules if r["severity"] == "blocker")
        warnings = sum(1 for r in self.batch_rules if r["severity"] == "warning")
        return {"blocker": blockers, "warning": warnings}


def load_contract(path: Path, model_name: str = "credit_application") -> Contract:
    with open(path, encoding="utf-8") as fh:
        return Contract(raw=yaml.safe_load(fh), model_name=model_name)


def field_checks(spec: dict[str, Any]) -> list[pa.Check]:
    checks: list[pa.Check] = []
    if "minimum" in spec:
        checks.append(pa.Check.ge(spec["minimum"]))
    if "exclusiveMinimum" in spec:
        checks.append(pa.Check.gt(spec["exclusiveMinimum"]))
    if "maximum" in spec:
        checks.append(pa.Check.le(spec["maximum"]))
    if "enum" in spec:
        checks.append(pa.Check.isin(spec["enum"]))
    return checks


def build_schema(contract: Contract, require_target: bool = False, extra_columns: dict[str, pa.Column] | None = None) -> pa.DataFrameSchema:
    columns: dict[str, pa.Column] = {}
    for name, spec in contract.fields.items():
        required = bool(spec.get("required", False)) or (require_target and name == "target")
        columns[name] = pa.Column(
            _DTYPES[spec["type"]],
            checks=field_checks(spec),
            nullable=not required,
            unique=bool(spec.get("unique", False)),
            required=required or name not in {"target", "gender", "family_status", "goods_price"},
            coerce=True,
            title=name,
        )
    if extra_columns:
        columns.update(extra_columns)
    return pa.DataFrameSchema(columns=columns, strict=True, coerce=True, name=f"{contract.model_name}@{contract.version}")


def feature_schema(numeric: list[str], categorical: list[str]) -> pa.DataFrameSchema:
    """Gate 2: contrato das features após a transformação (entrada do modelo)."""
    cols: dict[str, pa.Column] = {}
    finite = pa.Check(lambda s: ~s.isin([float("inf"), float("-inf")]), element_wise=False, name="finite")
    for c in numeric:
        cols[c] = pa.Column("float64", checks=[finite], nullable=c in {"goods_price", "ext_source_2", "ext_source_3"}, coerce=True)
    for c in ["debt_to_income", "credit_to_income"]:
        if c in cols:
            cols[c] = pa.Column("float64", checks=[finite, pa.Check.gt(0)], nullable=False, coerce=True)
    for c in categorical:
        cols[c] = pa.Column("string", nullable=False, coerce=True)
    return pa.DataFrameSchema(columns=cols, strict="filter", coerce=True, name="model_features")
