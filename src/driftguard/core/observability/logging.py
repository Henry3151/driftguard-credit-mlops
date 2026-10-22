"""Logs estruturados em JSON (um evento por linha) correlacionados por run_id/trace_id/span_id.

Todos os estágios escrevem em ``logs/pipeline.jsonl`` — a fonte centralizada de eventos
de sucesso/falha de ingestão, transformação, treino e resultados de drift.
Regra de privacidade: nunca registrar identificadores brutos ou valores de PII.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftguard.adapters import otel
from driftguard.core.observability import metrics
from driftguard.settings import get_settings


class _Run:
    def __init__(self) -> None:
        self.run_id = os.environ.get("DRIFTGUARD_RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(
            3
        )
        self.trace_id = secrets.token_hex(16)
        self.span_stack: list[tuple[str, str]] = []

    @property
    def current(self) -> tuple[str | None, str | None]:
        return self.span_stack[-1] if self.span_stack else (None, None)


RUN = _Run()
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def _log_path() -> Path:
    path = get_settings().paths.logs / "pipeline.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, Path):
        return value.as_posix()
    return value


def log_event(event: str, level: str = "INFO", status: str | None = None, **details: Any) -> dict[str, Any]:
    stage, span_id = RUN.current
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "level": level,
        "run_id": RUN.run_id,
        "trace_id": RUN.trace_id,
        "span_id": span_id,
        "stage": details.pop("stage", stage),
        "event": event,
        "status": status,
        **_jsonable(details),
    }
    with open(_log_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    if _LEVELS.get(level, 20) >= _LEVELS.get(os.environ.get("DRIFTGUARD_CONSOLE_LEVEL", "INFO"), 20):
        extra = {k: v for k, v in record.items() if k not in {"ts", "run_id", "trace_id", "span_id", "level", "event", "stage"}}
        print(
            f"[{level:<8}] {record['stage'] or '-':<10} {event:<28} {json.dumps(extra, ensure_ascii=False, default=str)[:220]}",
            file=sys.stderr,
        )
    return record


@contextmanager
def stage(name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
    """Span de um estágio do pipeline: loga início/fim, duração, status e exporta métricas."""
    span_id = secrets.token_hex(8)
    RUN.span_stack.append((name, span_id))
    ctx: dict[str, Any] = {"rows": None}
    start = time.perf_counter()
    log_event("STAGE_STARTED", status="running", **attrs)
    with otel.span(name, trace_attrs={"driftguard.run_id": RUN.run_id, **attrs}):
        try:
            yield ctx
        except SystemExit as exc:
            duration = time.perf_counter() - start
            ok = exc.code in (0, None)
            log_event(
                "STAGE_SUCCEEDED" if ok else "STAGE_FAILED",
                level="INFO" if ok else "ERROR",
                status="success" if ok else "failed",
                duration_ms=round(duration * 1000, 1),
                rows=ctx.get("rows"),
                exit_code=exc.code,
            )
            metrics.observe_stage(name, duration, ok, ctx.get("rows"))
            RUN.span_stack.pop()
            raise
        except Exception as exc:
            duration = time.perf_counter() - start
            log_event(
                "STAGE_FAILED",
                level="ERROR",
                status="failed",
                duration_ms=round(duration * 1000, 1),
                error=type(exc).__name__,
                message=str(exc)[:500],
                traceback=traceback.format_exc(limit=3)[-1500:],
            )
            metrics.observe_stage(name, duration, False, ctx.get("rows"))
            RUN.span_stack.pop()
            raise
    duration = time.perf_counter() - start
    log_event("STAGE_SUCCEEDED", status="success", duration_ms=round(duration * 1000, 1), rows=ctx.get("rows"), **metrics.sample_process())
    metrics.observe_stage(name, duration, True, ctx.get("rows"))
    RUN.span_stack.pop()


def read_events(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or _log_path()
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
