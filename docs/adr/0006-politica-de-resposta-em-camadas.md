# ADR-0006: Política de resposta em camadas e guardrails do challenger

- **Status:** Aceito
- **Data:** 2026-10-22

## Contexto

Retreinar a cada oscilação gera instabilidade e custo. Nunca retreinar deixa o modelo envelhecer. Rótulos de crédito chegam com atraso.

## Decisão

| Severidade | Gatilho | Resposta |
|---|---|---|
| Estável | PSI < 0,10 e sem queda de AUC | Monitoramento normal |
| Leve | 0,10 ≤ PSI < 0,25 em feature de baixa importância | Aumentar frequência de monitoramento |
| Moderada | PSI ≥ 0,25 em feature importante ou AUC −3 p.p. | Recalibrar limiar por custo + investigar |
| Severa | Drift **e** queda de AUC além do SLO (alerta composto) | Regra temporária + retreino challenger |

O challenger só é promovido (alias `champion`) se:

1. for melhor no global;
2. **não for pior** nos segmentos críticos;
3. respeitar a regra dos 4/5 de fairness no atributo protegido (gênero);
4. tiver Brier dentro do SLO.

Caso contrário, o champion atual é mantido e a decisão é registrada.

Todas as respostas possíveis (nenhuma ação, recalibração do limiar, regra temporária no segmento e retreino) são
avaliadas lado a lado na janela mais recente — custo esperado, perda realizada, aprovação e fairness — e a recomendação
é a de menor custo que respeita os guardrails (`reports/model/response_options.csv`).

## Alternativas consideradas

- **Retreino agendado fixo:** simples, mas reage tarde a choques abruptos.
- **Aprendizado online:** risco de aprender ruído (dilema estabilidade × plasticidade).

## Consequências

- (+) Resposta proporcional, auditável e alinhada ao custo do erro.
- (−) Exige definir custos (`configs/costs.yaml`) com premissas documentadas.
