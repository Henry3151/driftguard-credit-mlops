# Runbook de alertas

Todo alerta emitido (`reports/alerts.jsonl`) aponta para uma seção deste documento.
Prioridades: **P1** resposta em minutos (on-call 24/7) · **P2** em horas (horário comercial) · **P3** em dias.
Escalonamento de P1 sem resposta: 15 min → líder técnico · 30 min → gerente · 1 h → diretoria.

---

## pipeline-failure

**Significado:** um estágio terminou com `STAGE_FAILED`.

**Investigar:**
1. Filtre `logs/pipeline.jsonl` pelo `run_id` e pelo `event = STAGE_FAILED`.
2. Leia os campos `error`, `message` e `traceback`.
3. Use o `trace_id` para encontrar os eventos anteriores da mesma execução.

**Mitigar:** reexecute o estágio (todos são idempotentes). Se a falha estiver em uma dependência
externa, mantenha o último modelo/score válido (fallback) e abra um incidente.

**Escalar:** se a falha persistir por 2 execuções seguidas.

## data-quality

**Significado:** o contrato bloqueou o lote (P1) ou mandou linhas para a quarentena (P2/P3).

**Investigar:**
1. Abra `data/quarantine/<run>_<gate>_<lote>/failure_cases.csv`. O `reason_code` indica a regra violada.
2. Classifique a falha:
   - **estrutural** (coluna nova/removida, tipo mudou): o sistema produtor alterou o schema;
   - **de conteúdo** (faixas, nulos, duplicatas): problema de origem ou de integração.
3. Confirme se o lote já foi usado para treino (*blast radius*).

**Mitigar:**
- não force a ingestão;
- acione o dono do contrato (`info.owner`);
- mudança que quebra o contrato exige nova versão (`v2`) e o prazo de aviso combinado.

**Escalar:** dados corrompidos já usados em treino → P1 com Risco.

## data-drift

**Significado:** PSI moderado/severo ou KS/χ² significativo (após BH e efeito mínimo) em uma feature.

**Investigar:**
1. Abra o relatório Evidently da janela (`reports/drift/evidently_<janela>.html`).
2. Compare as distribuições da feature.
3. Verifique se é drift **natural** (economia, público) ou **técnico** (ETL, unidade, encoding).
   Um drift técnico se corrige no pipeline, não no modelo.
4. Cruze com a importância da feature (`impact_score`). Drift em feature irrelevante é só monitorado.

**Mitigar:** siga a política de resposta (`policy_level` no `window_metrics.csv`).

## multivariate-drift

**Significado:** MMD² significativo. A relação conjunta entre renda, crédito, parcela e idade mudou,
mesmo que cada variável isolada pareça estável.

**Investigar:** razões derivadas (`credit_to_income`, `debt_to_income`) e mudanças de política comercial
(limites automáticos, novos canais).

**Mitigar:** revisar regras de concessão. Se houver queda de desempenho, tratar como `concept-drift`.

## concept-drift

**Significado (P1):** AUC abaixo do SLO. Com drift simultâneo nos dados, é um **alerta composto**.

**Investigar:**
1. Leia `reports/causal/attribution.md`: a decomposição separa mudança de população × mudança de conceito.
2. Identifique o segmento onde o risco real descolou do previsto.

**Mitigar (em camadas):**
1. **Imediato:** regra de negócio temporária no segmento afetado (revisão manual ou limite reduzido).
2. **Curto prazo:** recalibrar o limiar pelo custo esperado.
3. **Estrutural:** retreino challenger com as janelas já rotuladas. A promoção só acontece se passar
   nos guardrails (`reports/model/challenger_evaluation.json`).

**Escalar:** Risco de Crédito sempre é notificado.

## model-calibration

**Significado:** Brier acima de 1,15 × referência. As probabilidades deixaram de refletir o risco real.

**Mitigar:** recalibrar (Platt/isotônica) com dados recentes rotulados e revisar o limiar de decisão.

## fairness

**Significado:**
- **P2:** *disparate impact* abaixo de 0,80 no atributo protegido (gênero);
- **P3:** gap de *equal opportunity* elevado na faixa etária (monitorada).

**Investigar:**
1. Taxas de aprovação e AUC por grupo (`reports/monitoring/fairness_by_window.csv`).
2. Procure features *proxy* do atributo protegido.

**Mitigar:** reponderação no treino, revisão de features, avaliação jurídica antes de qualquer limiar
por grupo. Nenhum modelo que viole a regra dos 4/5 é promovido.

**Escalar:** DPO e Compliance.

## business-impact

**Significado:** a perda realizada da carteira aprovada superou a perda esperada pelo modelo
(warning > 10%, critical > 25%). O modelo está **subestimando o risco**.

**Mitigar:** mesma sequência de `concept-drift`, priorizando a regra temporária no segmento enquanto o
retreino é validado.
