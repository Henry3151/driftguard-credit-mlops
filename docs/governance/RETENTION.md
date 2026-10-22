# Política de retenção

Implementada em `src/driftguard/core/governance/retention.py` (`python -m driftguard retention`; dry-run
por padrão e `--apply` para eliminar). Os prazos são proposta técnica e devem ser validados pelo DPO e
pelo Jurídico, considerando obrigações regulatórias do setor financeiro.

| Artefato | Local | Prazo | Justificativa |
|---|---|---|---|
| Lotes/linhas em quarentena | `data/quarantine/` | **30 dias** | Tempo para investigar a causa raiz; contêm dados inválidos |
| Logs operacionais | `logs/` | **90 dias** | Investigação de incidentes e auditoria operacional |
| Janelas de monitoramento | `data/production/` | **365 dias** | Comparação sazonal e auditoria de desempenho |
| Referência de treino + modelo | `data/reference/`, MLflow | **vida útil do modelo + 5 anos** | Reprodutibilidade e prestação de contas de decisões de crédito |
| Relatórios agregados | `reports/` | Indeterminado | Não contêm dados pessoais individuais (verificado pelo `pii-scan`) |

**Término do tratamento (LGPD arts. 15 e 16):** após o prazo, os dados são eliminados e a eliminação
é registrada em log (`RETENTION_APPLIED`). Pedidos de eliminação do titular são atendidos via
pseudônimo, preservando apenas o que a lei exigir.
