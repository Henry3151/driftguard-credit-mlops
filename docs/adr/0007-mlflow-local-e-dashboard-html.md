# ADR-0007: MLflow local e dashboard HTML estático (Grafana opcional)

- **Status:** Aceito
- **Data:** 2026-10-22

## Contexto

As opções avaliadas foram rastreamento com MLflow e dashboards de métricas (Datadog, nuvem ou open-source). O objetivo é mostrar a saúde do pipeline, a infraestrutura e os alertas de degradação de forma visual e reproduzível.

## Decisão

Usar os dois, com papéis distintos:

- **MLflow** (backend SQLite, `mlruns/`): ciclo de vida do modelo (experimento `training`) e um run por janela de monitoramento (experimento `monitoring`).
- **Dashboard HTML autocontido** (Plotly): visão operacional em 3 níveis. Abre no navegador sem servidor.
- **prometheus-client:** expõe as mesmas métricas no formato Prometheus (arquivo `.prom`). Grafana com dashboard provisionado fica opcional (ADR-0001).

## Alternativas consideradas

- **Datadog:** exige conta e agente.
- **Só Grafana:** exige Docker para visualizar qualquer coisa.

## Consequências

- (+) Evidência visual imediata e trilha de experimentos.
- (−) O HTML é estático, um retrato da execução; o monitoramento contínuo de verdade fica na opção Grafana.
