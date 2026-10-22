"""DAG opcional do Airflow (ADR-0001): orquestra os MESMOS comandos da CLI, sem duplicar lógica.

Recursos de produção: retries com backoff exponencial, SLA por tarefa, callback de falha,
execução diária e o bloqueio do contrato (exit code 2) interrompendo o fluxo a jusante.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

PROJECT_DIR = "{{ var.value.get('driftguard_project_dir', '/opt/driftguard') }}"


def _on_failure(context):  # pragma: no cover - executado pelo Airflow
    ti = context["task_instance"]
    print(f"[ALERTA] tarefa {ti.task_id} falhou no run {context['run_id']} — ver docs/RUNBOOK.md#pipeline-failure")


default_args = {
    "owner": "ml-engineering",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "on_failure_callback": _on_failure,
}

with DAG(
    dag_id="credit_risk_monitoring",
    description="Contratos de dados, monitoramento de drift e governança do modelo de credit scoring",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["credit-scoring", "mlops", "monitoring"],
) as dag:

    def cli(task_id: str, command: str, sla_minutes: int) -> BashOperator:
        return BashOperator(
            task_id=task_id, bash_command=f"cd {PROJECT_DIR} && python -m driftguard {command}", sla=timedelta(minutes=sla_minutes)
        )

    ingest = cli("ingest_and_validate", "ingest", 15)
    train = cli("train_baseline", "train", 30)
    simulate = cli("simulate_production", "simulate", 10)
    monitor = cli("monitor_windows", "monitor", 30)
    retrain = cli("evaluate_responses", "retrain", 30)
    causal = cli("causal_analysis", "causal", 10)
    pii = cli("pii_scan", "pii-scan", 5)
    dashboard = cli("build_dashboard", "dashboard", 5)

    ingest >> train >> simulate >> monitor >> [retrain, causal] >> pii >> dashboard
