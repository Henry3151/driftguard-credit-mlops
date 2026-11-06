
# Runbook de alertas

Todo alerta emitido em `reports/alerts.jsonl` aponta para uma seção deste documento. Este runbook orienta a investigação, a mitigação, o escalonamento e o registro das decisões tomadas durante a operação do pipeline de credit scoring.

**Prioridades e atendimento:**

| Prioridade | Prazo de resposta | Atendimento |
|---|---|---|
| **P1 — crítico** | Em minutos | On-call 24/7 |
| **P2 — atenção** | Em horas | Horário comercial |
| **P3 — informativo** | Em dias | Rotina de acompanhamento |

**Escalonamento de P1 sem resposta:** 15 min → líder técnico · 30 min → gerente · 1 h → diretoria.

> **Nota sobre a demonstração:** as prioridades, os tempos de atendimento e os encaminhamentos deste runbook descrevem o processo operacional proposto. O ambiente acadêmico não demonstra, por si só, a existência de uma equipe on-call ou de integrações reais de notificação.

---

## pipeline-failure

**Significado:** um estágio terminou com `STAGE_FAILED`.

**Investigar:**

1. Filtre `logs/pipeline.jsonl` pelo `run_id` e pelo `event = STAGE_FAILED`.
2. Leia os campos `error`, `message` e `traceback`, quando presentes.
3. Use o `trace_id` para encontrar os eventos anteriores da mesma execução.
4. Verifique se a falha é **esperada**, como o bloqueio deliberado do lote inválido durante `bad-batch` ou `demo` (`exit_code = 2`). Nesse cenário, o bloqueio do contrato é um controle funcionando, e não uma indisponibilidade inesperada.

**Mitigar:** reexecute o estágio após investigar a causa (os estágios foram projetados para serem idempotentes). Se a falha estiver em uma dependência externa, mantenha o último modelo/score válido (*fallback*) e abra um incidente.

**Escalar:** se a falha inesperada persistir por duas execuções consecutivas. Bloqueio de dados inválidos deve seguir também [data-quality](#data-quality).

## data-quality

**Significado:** o contrato bloqueou o lote (P1) ou encaminhou linhas para quarentena (P2/P3).

**Investigar:**

1. Abra `data/quarantine/<run>_<gate>_<lote>/failure_cases.csv`, se gerado. O `reason_code` indica a regra violada.
2. Classifique a falha:
   - **Estrutural:** coluna nova/removida ou alteração de tipo; pode indicar mudança do *schema* pelo sistema produtor.
   - **De conteúdo:** faixas inválidas, nulos ou duplicatas; pode indicar problema na origem ou na integração.
3. Confirme se o lote já foi usado para treinamento (*blast radius*).
4. Verifique os resultados de cada *gate* e o relatório de validação correspondente.

**Mitigar:**

- Não force a ingestão.
- Acione o dono do contrato (`info.owner`).
- Mudanças incompatíveis com o contrato exigem uma nova versão (por exemplo, `v2`) e comunicação conforme o prazo acordado.

**Escalar:** dados corrompidos já usados em treinamento → P1 com Risco de Crédito.

## data-drift

**Significado:** PSI moderado/severo ou KS/χ² significativo (após correção BH e avaliação do efeito mínimo) em uma *feature*.

**Investigar:**

1. Abra o relatório Evidently da janela (`reports/drift/evidently_<janela>.html`).
2. Compare as distribuições entre referência e produção.
3. Verifique se é drift **natural** (economia, público) ou **técnico** (ETL, unidade, *encoding*). Drift técnico deve ser corrigido no pipeline, não necessariamente no modelo.
4. Cruze com a importância da *feature* (`impact_score`). Drift em variável pouco relevante pode justificar apenas monitoramento, conforme a política.

**Mitigar:** siga a política de resposta (`policy_level` em `reports/monitoring/window_metrics.csv`). Não altere limiares apenas para forçar um retreinamento.

## multivariate-drift

**Significado:** MMD² significativo. A relação conjunta entre renda, crédito, parcela e idade mudou, mesmo que variáveis isoladas pareçam estáveis.

**Investigar:** examine razões derivadas (`credit_to_income`, `debt_to_income`) e eventuais mudanças de política comercial (limites automáticos, novos canais).

**Mitigar:** revise as regras de concessão. Se houver queda de desempenho, investigue também [concept-drift](#concept-drift).

## concept-drift

**Significado (P1):** AUC abaixo do SLO. Com drift simultâneo nos dados, pode configurar um **alerta composto**.

**Investigar:**

1. Leia `reports/causal/attribution.md`: a decomposição apresentada pelo projeto distingue efeitos estimados de mudança de população e de mudança de conceito.
2. Identifique o segmento em que o risco observado se descolou do previsto.
3. Confira a disponibilidade e a qualidade dos rótulos recentes antes de concluir que houve alteração do conceito.

**Mitigar (em camadas):**

1. **Imediato:** considerar regra temporária de negócio no segmento afetado (revisão manual ou limite reduzido), sujeita à aprovação da área responsável.
2. **Curto prazo:** avaliar calibração e limiar de decisão segundo o custo esperado.
3. **Estrutural:** avaliar um challenger com janelas já rotuladas. A promoção só acontece após aprovação dos *guardrails* (`reports/model/challenger_evaluation.json`).

**Escalar:** notificar Risco de Crédito conforme o procedimento proposto.

## model-calibration

**Significado:** Brier acima de 1,15 × referência. As probabilidades podem ter deixado de refletir adequadamente o risco observado.

**Investigar:** compare o Brier por janela, a inadimplência observada, a PD média prevista e eventuais alterações na composição da população.

**Mitigar:** avaliar recalibração (Platt/isotônica) com dados recentes rotulados e revisar o limiar de decisão. Validar o resultado antes de alterar o modelo em produção.

## fairness

**Significado:**

- **P2:** *disparate impact* abaixo de 0,80 no atributo protegido (gênero).
- **P3:** *gap* de *equal opportunity* elevado na faixa etária monitorada.

**Investigar:**

1. Taxas de aprovação e AUC por grupo (`reports/monitoring/fairness_by_window.csv`).
2. Possíveis *features proxy* de atributos protegidos.
3. Impactos de quaisquer medidas corretivas sobre desempenho e disparidades.

**Mitigar:** avaliar reponderação no treino, revisão de *features* e avaliação jurídica antes de qualquer limiar por grupo. Conforme a política definida no projeto, nenhum modelo que viole a regra dos 4/5 deve ser promovido.

**Escalar:** DPO e Compliance, conforme a governança proposta.

## business-impact

**Significado:** a perda realizada da carteira aprovada superou a perda esperada pelo modelo (**warning > 10%**, **critical > 25%**). É um sinal de possível subestimação de risco que exige investigação de dados, calibração, condições de negócio e desempenho.

**Mitigar:** seguir a mesma sequência de [concept-drift](#concept-drift), priorizando a avaliação de medidas temporárias para o segmento afetado enquanto as alternativas são verificadas.

### Tratamento de alertas P1 sem retreinamento automático

#### Objetivo

Garantir que impactos financeiros críticos sejam investigados mesmo quando a política de monitoramento não classificar a degradação do modelo como **severa** e, portanto, não acionar retreinamento.

#### Evidência da execução demonstrativa

Na execução local de `python -m driftguard demo`, com dados **sintéticos**, foram observados:

| Indicador | Resultado observado |
|---|---|
| Alertas P1 no total da execução | 5 |
| Alertas P2 no total da execução | 20 |
| Perda realizada em relação à esperada na última janela | +78% |
| Política operacional da última janela | Moderado |
| Retreinamento | Não acionado (`RETRAIN_SKIPPED`) |

Os cinco alertas P1 reportados no cenário demonstrativo foram de impacto financeiro. Esses valores descrevem **essa execução específica** e não devem ser generalizados para produção real.

**Interpretação:** a prioridade do alerta de negócio e a severidade da política de retreinamento são avaliações distintas. `RETRAIN_SKIPPED` não representa, por si só, falha do pipeline, mas também **não elimina o risco financeiro sinalizado**.

#### Procedimento de resposta ao P1

1. **Identificar o alerta:** registrar `run_id`, janela, horário, tipo, severidade, título e rota de atendimento. Consulte `reports/alerts.jsonl`, `logs/pipeline.jsonl` e o dashboard.
2. **Verificar a qualidade dos dados:** confira os relatórios de contratos, quarentenas e *Gates* 1, 2 e 3. Avalie a hipótese de problema de integração ou de transformação.
3. **Investigar drift:** examine o Evidently e os indicadores PSI, KS e MMD. Identifique as variáveis e segmentos afetados.
4. **Avaliar desempenho e calibração:** compare AUC, KS, Brier, PD média e inadimplência observada; considere a disponibilidade dos rótulos.
5. **Dimensionar o impacto financeiro:** compare perda realizada e esperada, janela, segmento e tamanho da carteira afetada. Distinga resultado simulado de resultado efetivamente observado em produção.
6. **Examinar a análise causal:** consulte `reports/causal/attribution.md`. Trate a decomposição como evidência analítica do projeto, a ser confrontada com outras hipóteses e limitações.
7. **Escalar e definir responsáveis:** encaminhe a ocorrência a Risco de Crédito e ML Engineering, conforme a prioridade P1 e o plano de escalonamento do runbook.
8. **Decidir a resposta:** considerar investigação da origem, monitoramento reforçado, medidas temporárias de negócio, recalibração ou treinamento de challenger. Documente por que o retreinamento foi ou não acionado.
9. **Registrar e acompanhar:** documente evidências, decisão, responsáveis, prazos, ações e critério para encerramento.

#### Critérios para retreinamento e promoção

- **Alerta P1 financeiro:** exige investigação e encaminhamento; não implica retreinamento automático.
- **Retreinamento:** segue os critérios efetivamente implementados na política de resposta do projeto, com base na evidência técnica disponível.
- **Challenger:** pode ser avaliado com dados recentes e rótulos disponíveis; sua promoção depende dos *guardrails* de desempenho, calibração, fairness e impacto financeiro.
- **Mudança dos limiares:** não reduzir limites estatísticos ou operacionais somente para produzir retreinamento na demonstração. Toda alteração da política deve ser justificada, testada e documentada.

#### Evidências para auditoria

| Artefato | Finalidade |
|---|---|
| `reports/alerts.jsonl` | Alertas e prioridades emitidos |
| `logs/pipeline.jsonl` | Sequência de eventos, *stages* e decisões |
| `reports/monitoring/window_metrics.csv` | Desempenho, qualidade, drift e impacto por janela |
| `reports/drift/` | Evidências estatísticas e relatórios Evidently |
| `reports/causal/attribution.md` | Investigação dos efeitos estimados |
| `reports/model/challenger_evaluation.json` | Avaliação de challenger, quando executada |
| `reports/dashboard/index.html` | Visão consolidada para acompanhamento |

A ausência de um artefato condicional (como avaliação do challenger quando o retreinamento não foi acionado) deve ser registrada como **não aplicável**, não como falha automática.

#### Modelo de registro da decisão

Copie e preencha este modelo para cada ocorrência P1 investigada. O documento descreve um procedimento recomendado; **não afirma que um sistema de tickets já esteja integrado ou operante**.

```text
ID da ocorrência:
Data e hora (com fuso):
Run ID / janela:
Tipo e prioridade do alerta:
Responsável pela investigação:
Impacto financeiro e população afetada:
Qualidade dos dados / resultado dos gates:
Evidências de drift e desempenho:
Hipóteses e análise causal:
Política de resposta / motivo de RETRAIN_SKIPPED, se houver:
Decisão: monitorar | corrigir dados | mitigar | recalibrar | avaliar challenger
Justificativa:
Responsável pela ação e prazo:
Aprovação / escalonamento:
Evidências vinculadas:
Status e critério de encerramento:
```

#### Resultado esperado

Cada alerta P1 deve ter **uma investigação e uma decisão rastreáveis**, ainda que a decisão seja manter o modelo e monitorar. A ausência de retreinamento automático não deve ser interpretada como ausência de risco financeiro.
