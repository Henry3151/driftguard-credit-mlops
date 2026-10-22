"""Configuração tipada do projeto (paths, YAMLs e variáveis de ambiente)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("DRIFTGUARD_ROOT", Path(__file__).resolve().parents[2]))


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    configs: Path = ROOT / "configs"
    contracts: Path = ROOT / "contracts"
    data: Path = ROOT / "data"
    raw: Path = ROOT / "data" / "raw"
    reference: Path = ROOT / "data" / "reference"
    production: Path = ROOT / "data" / "production"
    quarantine: Path = ROOT / "data" / "quarantine"
    audit: Path = ROOT / "data" / "audit"
    models: Path = ROOT / "models"
    reports: Path = ROOT / "reports"
    logs: Path = ROOT / "logs"

    def ensure(self) -> None:
        for p in (self.raw, self.reference, self.production, self.quarantine, self.audit, self.models, self.reports, self.logs):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    paths: Paths = field(default_factory=Paths)
    dataset: dict[str, Any] = field(default_factory=dict)
    scenario: dict[str, Any] = field(default_factory=dict)
    slo: dict[str, Any] = field(default_factory=dict)
    costs: dict[str, Any] = field(default_factory=dict)
    contract_path: Path = ROOT / "contracts" / "credit_application.v1.yaml"
    mlflow_uri: str = os.environ.get("MLFLOW_TRACKING_URI", f"sqlite:///{(ROOT / 'mlflow.db').as_posix()}")
    # Salt da pseudonimização: NUNCA versionado. Em produção vem de um cofre de segredos.
    pseudonym_salt: str = os.environ.get("DRIFTGUARD_PSEUDONYM_SALT", "dev-only-salt-change-me")
    otel_enabled: bool = os.environ.get("DRIFTGUARD_OTEL_ENABLED", "false").lower() == "true"
    pushgateway_url: str | None = os.environ.get("DRIFTGUARD_PUSHGATEWAY_URL")
    seed: int = 42

    @property
    def numeric_features(self) -> list[str]:
        return list(self.dataset["features"]["numeric"])

    @property
    def categorical_features(self) -> list[str]:
        return list(self.dataset["features"]["categorical"])

    @property
    def features(self) -> list[str]:
        return self.numeric_features + self.categorical_features


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    paths = Paths()
    return Settings(
        paths=paths,
        dataset=_load_yaml(paths.configs / "dataset.yaml"),
        scenario=_load_yaml(paths.configs / "drift_scenario.yaml"),
        slo=_load_yaml(paths.configs / "slo.yaml"),
        costs=_load_yaml(paths.configs / "costs.yaml"),
    )
