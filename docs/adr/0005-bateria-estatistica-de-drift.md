# ADR-0005: Bateria estatística de drift e Evidently como relatório oficial

- **Status:** Aceito
- **Data:** 2026-10-22

## Contexto

PSI e KS são univariados: não detectam mudança de correlação. Com muitas features × janelas e amostras grandes, o p-valor isolado gera falsos positivos. A organização padronizou o Evidently AI como relatório de drift auditável, e o mercado de crédito usa PSI/KS como linguagem comum com Risco.

## Decisão

1. **PSI** por feature (bins pelos quantis da referência): mede o tamanho da mudança. Faixas: < 0,10 estável; 0,10–0,25 moderado; ≥ 0,25 severo.
2. **KS** (numéricas) e **χ²** (categóricas) com correção **Benjamini-Hochberg** e efeito mínimo D ≥ 0,10: diz se a mudança é real.
3. **MMD** com kernel RBF e p-valor por permutação: detecta drift multivariado.
4. **ADWIN** sobre o score: localiza o ponto de mudança no tempo.
5. **PSI × importância do modelo (ganho):** prioriza os alertas pelo impacto.
6. **Evidently** (`DataDriftPreset` e `ClassificationPreset`): relatório HTML oficial por janela.

PSI e KS são implementados no projeto e validados contra valores conhecidos, servindo de checagem independente do Evidently.

## Alternativas consideradas

- **Só Evidently:** atende ao mínimo, mas não mostra domínio estatístico nem cobre drift multivariado.
- **Alibi Detect:** equivalente para MMD, porém mais pesado (dependências de TF/PyTorch).

## Consequências

- (+) Mostra *quanto*, *se é real*, *quando* e *se importa*.
- (−) Mais métricas para explicar; o dashboard as organiza por pergunta.
