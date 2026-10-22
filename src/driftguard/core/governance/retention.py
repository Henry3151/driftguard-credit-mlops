"""Política de retenção executável (LGPD art. 15/16 — término do tratamento).

Prazos propostos (validar com o DPO): ver docs/governance/RETENTION.md.
Modo padrão: dry-run (apenas lista o que seria eliminado e registra em log).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from driftguard.core.observability.logging import log_event

POLICY_DAYS = {
    "data/quarantine": 30,  # lotes rejeitados: tempo para investigação
    "logs": 90,  # logs operacionais
    "data/production": 365,  # janelas de monitoramento (auditoria do modelo)
}


def apply_retention(root: Path, dry_run: bool = True, now: float | None = None) -> list[dict]:
    now = now or time.time()
    actions = []
    for rel, days in POLICY_DAYS.items():
        base = root / rel
        if not base.exists():
            continue
        for item in base.iterdir():
            age_days = (now - item.stat().st_mtime) / 86400
            if age_days > days:
                actions.append({"path": item.relative_to(root).as_posix(), "age_days": round(age_days, 1), "policy_days": days})
                if not dry_run:
                    shutil.rmtree(item) if item.is_dir() else item.unlink()
    log_event("RETENTION_APPLIED", dry_run=dry_run, items=len(actions))
    return actions
