"""Motor de alertas acionáveis: severidade, prioridade, roteamento, deduplicação e runbook."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftguard.core.observability import metrics
from driftguard.core.observability.logging import RUN, log_event

ROUTES = {
    "PIPELINE_FAILURE": "engenharia-de-dados (on-call)",
    "DATA_QUALITY": "engenharia-de-dados",
    "DATA_DRIFT": "ml-engineering",
    "MULTIVARIATE_DRIFT": "ml-engineering",
    "CONCEPT_DRIFT": "ml-engineering + risco-de-credito",
    "MODEL_CALIBRATION": "ml-engineering",
    "FAIRNESS": "risco-e-compliance (DPO)",
    "BUSINESS_IMPACT": "risco-de-credito",
}
PRIORITY = {"critical": "P1", "warning": "P2", "info": "P3"}
RUNBOOK = "docs/RUNBOOK.md#"


@dataclass
class Alert:
    type: str
    severity: str
    title: str
    window: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    action: str | None = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    run_id: str = field(default_factory=lambda: RUN.run_id)

    @property
    def priority(self) -> str:
        return PRIORITY[self.severity]

    @property
    def route(self) -> str:
        return ROUTES.get(self.type, "ml-engineering")

    @property
    def runbook(self) -> str:
        return RUNBOOK + self.type.lower().replace("_", "-")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "priority": self.priority, "route": self.route, "runbook": self.runbook}


class AlertManager:
    def __init__(self, path: Path, reset: bool = False) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if reset and self.path.exists():
            self.path.unlink()
        self._seen: set[tuple[str, str | None, str]] = set()
        self.fired: list[Alert] = []

    def fire(self, alert: Alert, dedup_key: str = "") -> bool:
        key = (alert.type, alert.window, dedup_key or alert.title)
        if key in self._seen:  # deduplicação
            return False
        self._seen.add(key)
        self.fired.append(alert)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(alert.to_dict(), ensure_ascii=False, default=str) + "\n")
        metrics.ALERTS.labels(alert.severity, alert.type).inc()
        level = {"critical": "CRITICAL", "warning": "WARNING", "info": "INFO"}[alert.severity]
        log_event(
            "ALERT_FIRED",
            level=level,
            alert_type=alert.type,
            severity=alert.severity,
            priority=alert.priority,
            window=alert.window,
            title=alert.title,
            route=alert.route,
            runbook=alert.runbook,
        )
        return True


def read_alerts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
