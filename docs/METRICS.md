# Métricas de saúde, SLIs e SLOs

Limiares configuráveis em [`configs/slo.yaml`](../configs/slo.yaml). Todas as métricas são expostas no
formato Prometheus (prefixo `driftguard_`), registradas no MLflow (runs de monitoramento) e
consolidadas no dashboard (`reports/dashboard/index.html`).

## 1. Pipeline (operação)

| Métrica | Tipo | SLI | SLO | Origem |
|---|---|---|---|---|
| `driftguard_stage_status{stage}` | gauge | 1 = sucesso, 0 = falha do último run | 99% dos estágios com sucesso | `logging.stage` |
| `driftguard_stage_duration_seconds{stage}` | gauge | duração do estágio | dentro do SLA do DAG (ex.: treino < 30 min) | `logging.stage` |
| `driftguard_stage_runs_total{stage,status}` | counter | execuções por status | base do *error budget* | `logging.stage` |
| `driftguard_rows_processed_total{stage}` | counter | volume processado | lote ≥ 1.000 linhas | `logging.stage` |
| `driftguard_batches_blocked_total{gate}` | counter | lotes bloqueados pelo contrato | 0 em lotes de produção saudáveis | `contracts.gates` |
| `driftguard_rows_rejected_total{reason}` | counter | linhas rejeitadas por regra | < 1% em lote saudável | `contracts.gates` |

## 2. Dados

| Métrica | SLI | SLO |
|---|---|---|
| `driftguard_data_quality_score{batch}` | DQ Score 0–100 (6 dimensões) | ≥ 95 |
| `driftguard_feature_psi{feature,window}` | PSI vs referência | < 0,10 estável · 0,10–0,25 moderado · ≥ 0,25 severo |
| `driftguard_drifted_features{window}` | nº de features com drift confirmado (PSI ou KS/χ² + BH + efeito mínimo) | 0 features importantes com PSI ≥ 0,25 |
| `driftguard_mmd_pvalue{window}` | p-valor do MMD (drift multivariado) | ≥ 0,05 |
| Freshness (contrato) | idade do lote | ≤ 12h (SLA de 24h) |

## 3. Modelo

| Métrica | SLI | SLO |
|---|---|---|
| `driftguard_model_auc{window}` | AUC com rótulo atrasado (1 janela) | ≥ AUC de referência − 3 p.p. |
| `driftguard_model_brier{window}` | calibração | ≤ 1,15 × Brier de referência |
| `driftguard_fairness_disparate_impact{attribute,window}` | razão entre taxas de aprovação | ≥ 0,80 no atributo protegido (gênero) |
| Gap de *equal opportunity* | aprovação entre bons pagadores | ≤ 0,15 (faixa etária, monitorada) |

## 4. Infraestrutura

| Métrica | SLI | SLO |
|---|---|---|
| `driftguard_prediction_latency_seconds` (histograma) | latência por lote de 1.000 linhas | p95 < 250 ms |
| `driftguard_process_memory_bytes` | memória de pico do processo | < 4 GB |
| `driftguard_process_cpu_seconds` | CPU consumida | acompanhamento de custo (FinOps) |

## 5. Negócio

| Métrica | SLI | SLO |
|---|---|---|
| `driftguard_expected_loss_brl{window}` | perda esperada da carteira aprovada (PD × crédito × LGD) | — |
| Gap de perda (`loss_gap`) | perda realizada ÷ esperada − 1 | ≤ 10% (warning) · > 25% (critical) |
| `driftguard_alerts_total{severity,type}` | alertas emitidos | P1 tratado em minutos (on-call) |

## 6. Eventos de log relevantes

| Evento | Significado |
|---|---|
| `STAGE_STARTED` / `STAGE_SUCCEEDED` / `STAGE_FAILED` | ciclo de vida de cada estágio, com `duration_ms` e `rows` |
| `GATE_PASSED` · `ROWS_QUARANTINED` · `INGESTION_BLOCKED` | decisão do contrato de dados |
| `DATA_QUALITY_WARNING` | regra *warning* violada |
| `PII_TREATED` · `PII_SCAN_DONE` | etapas de privacidade |
| `MODEL_TRAINED` · `CHALLENGER_EVALUATED` | ciclo de vida do modelo |
| `WINDOW_MONITORED` · `ADWIN_CHANGE_POINT` | monitoramento de drift |
| `ALERT_FIRED` | alerta com severidade, prioridade, rota e runbook |
| `OPTIONAL_COMPONENT_SKIPPED` | componente opcional ausente (o fluxo segue) |
