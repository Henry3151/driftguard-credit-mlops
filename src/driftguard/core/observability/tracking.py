"""Rastreamento com MLflow: ciclo de vida do modelo e um run por janela de monitoramento."""

from __future__ import annotations

import os
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import mlflow  # noqa: E402

from driftguard.core.observability.logging import RUN  # noqa: E402
from driftguard.settings import get_settings  # noqa: E402

EXP_TRAINING = "credit-scoring-training"
EXP_MONITORING = "credit-scoring-monitoring"
REGISTERED_MODEL = "credit-scoring-home-credit"

os.environ.setdefault("MLFLOW_ENABLE_ARTIFACTS_PROGRESS_BAR", "false")
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
warnings.filterwarnings("ignore", module="mlflow")


def configure() -> None:
    cfg = get_settings()
    mlflow.set_tracking_uri(cfg.mlflow_uri)
    os.environ.setdefault("MLFLOW_ARTIFACTS_DESTINATION", (cfg.paths.root / "mlartifacts").as_uri())


def _experiment(name: str) -> str:
    cfg = get_settings()
    exp = mlflow.get_experiment_by_name(name)
    if exp is None:
        return mlflow.create_experiment(name, artifact_location=(cfg.paths.root / "mlartifacts" / name).as_uri())
    return exp.experiment_id


@contextmanager
def run(experiment: str, run_name: str, tags: dict[str, Any] | None = None) -> Iterator[mlflow.ActiveRun]:
    configure()
    exp_id = _experiment(experiment)
    base_tags = {"driftguard.run_id": RUN.run_id, "driftguard.trace_id": RUN.trace_id}
    with mlflow.start_run(experiment_id=exp_id, run_name=run_name, tags={**base_tags, **(tags or {})}) as active:
        yield active


def log_dict_flat(prefix: str, values: dict[str, Any]) -> None:
    clean = {f"{prefix}{k}": float(v) for k, v in values.items() if isinstance(v, (int, float)) and v is not None}
    if clean:
        mlflow.log_metrics(clean)


def log_artifacts_if_exist(paths: list[Path], artifact_path: str | None = None) -> None:
    for p in paths:
        if p.exists():
            mlflow.log_artifact(str(p), artifact_path=artifact_path)


TRUSTED_TYPES = ["xgboost.core.Booster", "xgboost.sklearn.XGBClassifier"]


def log_sklearn_model(model, signature, input_example, registered_model_name: str):
    """Registra o pipeline sklearn. MLflow >= 3 serializa com skops e exige tipos confiáveis."""
    try:
        return mlflow.sklearn.log_model(
            model,
            name="model",
            signature=signature,
            input_example=input_example,
            registered_model_name=registered_model_name,
            skops_trusted_types=TRUSTED_TYPES,
        )
    except TypeError:  # versões do MLflow sem o parâmetro skops_trusted_types
        return mlflow.sklearn.log_model(
            model, name="model", signature=signature, input_example=input_example, registered_model_name=registered_model_name
        )
