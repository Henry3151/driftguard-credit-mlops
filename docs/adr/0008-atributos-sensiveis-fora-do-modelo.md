# ADR-0008: Atributos sensíveis fora das features; uso exclusivo em auditoria de vieses

- **Status:** Aceito
- **Data:** 2026-10-22

## Contexto

A LGPD (art. 6º, IX) proíbe tratamento discriminatório, e o art. 20 garante revisão de decisões automatizadas. Atributos como gênero, estado civil ou idade avançada podem gerar discriminação direta. Ao mesmo tempo, sem esses dados não é possível **medir** viés.

## Decisão

- Atributos sensíveis **não entram** como features do modelo.
- **Gênero** é tratado como atributo **protegido**: regra dos 4/5 é *guardrail* de bloqueio (alerta e veto de promoção de modelos).
- **Faixa etária** é **monitorada** (idade é fator de risco legítimo em crédito e é feature do modelo): gaps são reportados e alertados como informação, sem bloquear. Ficam numa tabela separada, ligada só pelo ID pseudonimizado, e são usados **exclusivamente** para métricas de fairness (*disparate impact* com regra dos 4/5, *equal opportunity*, AUC por grupo), medidas **por janela**.
- Relatórios publicam só agregados, em faixas, com supressão de grupos com n < 30.
- Cada decisão recebe **motivos** (top-3 SHAP) para atender ao art. 20.

## Alternativas consideradas

- **Remover os atributos completamente:** impede detectar viés por *proxy*.
- **Usar como feature:** risco jurídico e ético.

## Consequências

- (+) "Fairness through awareness": mede sem discriminar.
- (−) Proxies (ex.: região) ainda podem carregar viés. Mitigado pelo monitoramento contínuo de fairness e pela revisão periódica das features.
