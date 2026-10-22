"""Métricas no formato Prometheus (prometheus-client).

Caminho principal: exporta para arquivo texto (``reports/metrics/<comando>.prom``),
compatível com o *textfile collector* do node_exporter. Opcional: Pushgateway (ADR-0001).
"""

from __future__ import annotations

import os
from pathlib import Path

import psutil
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, write_to_textfile

REGISTRY = CollectorRegistry()

STAGE_DURATION = Gauge("driftguard_stage_duration_seconds", "Duração do último run de cada estágio", ["stage"], registry=REGISTRY)
STAGE_STATUS = Gauge("driftguard_stage_status", "1 = sucesso, 0 = falha (último run)", ["stage"], registry=REGISTRY)
STAGE_RUNS = Counter("driftguard_stage_runs_total", "Execuções de estágio por status", ["stage", "status"], registry=REGISTRY)
ROWS_PROCESSED = Counter("driftguard_rows_processed_total", "Linhas processadas por estágio", ["stage"], registry=REGISTRY)
ROWS_REJECTED = Counter("driftguard_rows_rejected_total", "Linhas rejeitadas pelo contrato", ["reason"], registry=REGISTRY)
BATCHES_BLOCKED = Counter("driftguard_batches_blocked_total", "Lotes bloqueados pelo circuit breaker", ["gate"], registry=REGISTRY)
DQ_SCORE = Gauge("driftguard_data_quality_score", "Data Quality Score (0-100)", ["batch"], registry=REGISTRY)
FEATURE_PSI = Gauge("driftguard_feature_psi", "PSI por feature e janela", ["feature", "window"], registry=REGISTRY)
DRIFTED_FEATURES = Gauge("driftguard_drifted_features", "Features com drift confirmado", ["window"], registry=REGISTRY)
MMD_PVALUE = Gauge("driftguard_mmd_pvalue", "p-valor do teste MMD multivariado", ["window"], registry=REGISTRY)
MODEL_AUC = Gauge("driftguard_model_auc", "AUC do modelo (rótulo atrasado)", ["window"], registry=REGISTRY)
MODEL_BRIER = Gauge("driftguard_model_brier", "Brier score (calibração)", ["window"], registry=REGISTRY)
EXPECTED_LOSS = Gauge("driftguard_expected_loss_brl", "Perda esperada da carteira aprovada", ["window"], registry=REGISTRY)
DISPARATE_IMPACT = Gauge("driftguard_fairness_disparate_impact", "Disparate impact por atributo", ["attribute", "window"], registry=REGISTRY)
ALERTS = Counter("driftguard_alerts_total", "Alertas emitidos", ["severity", "type"], registry=REGISTRY)
PRED_LATENCY = Histogram(
    "driftguard_prediction_latency_seconds",
    "Latência de predição por lote de 1.000 linhas",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    registry=REGISTRY,
)
MEMORY = Gauge("driftguard_process_memory_bytes", "Memória residente do processo (pico)", registry=REGISTRY)
CPU_SECONDS = Gauge("driftguard_process_cpu_seconds", "CPU consumida pelo processo", registry=REGISTRY)


def observe_stage(stage: str, duration: float, ok: bool, rows: int | None) -> None:
    STAGE_DURATION.labels(stage).set(duration)
    STAGE_STATUS.labels(stage).set(1 if ok else 0)
    STAGE_RUNS.labels(stage, "success" if ok else "failed").inc()
    if rows:
        ROWS_PROCESSED.labels(stage).inc(rows)


def sample_process() -> dict[str, float]:
    proc = psutil.Process(os.getpid())
    rss = proc.memory_info().rss
    try:  # pico de memória: 'resource' só existe em Unix
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    except Exception:  # pragma: no cover - Windows
        peak = getattr(proc.memory_info(), "peak_wset", rss)
    cpu = sum(proc.cpu_times()[:2])
    MEMORY.set(max(rss, peak))
    CPU_SECONDS.set(cpu)
    return {"memory_mb": round(max(rss, peak) / 2**20, 1), "cpu_seconds": round(cpu, 2)}


def export(command: str, out_dir: Path) -> Path:
    sample_process()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{command}.prom"
    write_to_textfile(str(path), REGISTRY)
    from driftguard.adapters import pushgateway  # opcional, import protegido

    pushgateway.push(REGISTRY, job=f"driftguard_{command}")
    return path
