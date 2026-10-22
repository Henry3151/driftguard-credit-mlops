"""Interface de linha de comando: ``python -m driftguard <comando>``.

Comandos (na ordem do pipeline):
  ingest        Qualidade — lê a fonte, aplica o contrato (Gate 1/2), privacidade e separa Referência × Produção
  bad-batch     Qualidade — gera um lote defeituoso e prova que o contrato BLOQUEIA a ingestão (exit 2)
  validate      Qualidade — valida um lote CSV arbitrário (exit 0 = aceito, 2 = bloqueado)
  train         Modelo — treina o modelo base, define limiar por custo e registra no MLflow (alias champion)
  simulate      Drift — cria 6 janelas mensais de produção com data drift e concept drift
  monitor       Monitoramento — gates, predição, PSI/KS/MMD, Evidently, desempenho, fairness, alertas, MLflow
  retrain       Resposta — treina challenger e aplica guardrails de promoção
  causal        Governança — análise causal (decomposição + ablação contrafactual)
  pii-scan      Governança — verifica que logs/relatórios não contêm PII
  retention     Governança — aplica a política de retenção (dry-run por padrão)
  dashboard     Observabilidade — gera reports/dashboard/index.html
  figures       Documentação — gera os gráficos PNG em docs/images
  all           Executa o pipeline completo
  demo          Pipeline completo com dados SINTÉTICOS (sem baixar o dataset)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from driftguard import pipeline
from driftguard.core.contracts.gates import IngestionBlocked
from driftguard.core.observability import metrics
from driftguard.core.observability.dashboard import build as build_dashboard
from driftguard.core.observability.logging import RUN, log_event, stage
from driftguard.settings import get_settings


def _dashboard() -> Path:
    cfg = get_settings()
    with stage("dashboard"):
        out = build_dashboard(cfg.paths.root, cfg.slo, cfg.paths.reports / "dashboard" / "index.html")
        log_event("DASHBOARD_BUILT", file=out.relative_to(cfg.paths.root))
    return out


def _figures() -> None:
    from driftguard.core.observability.figures import build_all

    cfg = get_settings()
    with stage("figures") as ctx:
        outs = build_all(cfg.paths.root, cfg.paths.root / "docs" / "images", cfg.slo)
        ctx["rows"] = len(outs)


def _all(synthetic_rows: int | None = None) -> None:
    pipeline.ingest(synthetic_rows=synthetic_rows)
    pipeline.bad_batch_demo()
    pipeline.train()
    pipeline.simulate_drift()
    pipeline.monitor()
    pipeline.retrain_challenger()
    pipeline.causal()
    pipeline.retention(dry_run=True)
    pipeline.scan_pii()
    _dashboard()
    if synthetic_rows is None:  # figuras da documentação só com dados reais
        _figures()
    _snapshot_logs()


def _snapshot_logs() -> None:
    """Cópia dos logs estruturados da execução como evidência versionável (sem PII — ver pii-scan)."""
    cfg = get_settings()
    src = cfg.paths.logs / "pipeline.jsonl"
    if src.exists():
        dst = cfg.paths.reports / "logs" / "pipeline.jsonl"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="driftguard", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest")
    sub.add_parser("bad-batch")
    v = sub.add_parser("validate")
    v.add_argument("--batch", type=Path, required=True)
    for name in ("train", "simulate", "monitor", "retrain", "causal", "pii-scan", "dashboard", "figures", "all"):
        sub.add_parser(name)
    r = sub.add_parser("retention")
    r.add_argument("--apply", action="store_true", help="elimina de fato (padrão: dry-run)")
    d = sub.add_parser("demo")
    d.add_argument("--rows", type=int, default=60_000)
    args = parser.parse_args(argv)

    cfg = get_settings()
    cfg.paths.ensure()
    log_event("COMMAND_STARTED", stage="cli", command=args.command, run_id=RUN.run_id)
    code = 0
    try:
        if args.command == "ingest":
            pipeline.ingest()
        elif args.command == "bad-batch":
            pipeline.validate_batch(bad=True)
        elif args.command == "validate":
            pipeline.validate_batch(path=args.batch)
        elif args.command == "train":
            pipeline.train()
        elif args.command == "simulate":
            pipeline.simulate_drift()
        elif args.command == "monitor":
            pipeline.monitor()
        elif args.command == "retrain":
            pipeline.retrain_challenger()
        elif args.command == "causal":
            pipeline.causal()
        elif args.command == "pii-scan":
            code = 1 if pipeline.scan_pii() else 0
        elif args.command == "retention":
            pipeline.retention(dry_run=not args.apply)
        elif args.command == "dashboard":
            _dashboard()
        elif args.command == "figures":
            _figures()
        elif args.command == "all":
            _all()
        elif args.command == "demo":
            _all(synthetic_rows=args.rows)
    except IngestionBlocked as exc:
        log_event("COMMAND_BLOCKED", level="CRITICAL", stage="cli", command=args.command, gate=exc.gate, reason=exc.reason)
        print(f"\n✘ LOTE BLOQUEADO pelo contrato de dados ({exc.gate}): {exc.reason}", file=sys.stderr)
        code = exc.code
    finally:
        res = metrics.sample_process()
        log_event("PROCESS_RESOURCES", stage="cli", command=args.command, **res)
        prom = metrics.export(args.command.replace("-", "_"), cfg.paths.reports / "metrics")
        log_event("COMMAND_FINISHED", stage="cli", command=args.command, exit_code=code, metrics_file=prom.relative_to(cfg.paths.root))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
