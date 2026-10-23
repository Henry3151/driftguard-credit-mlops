# Model Card — Credit Scoring (XGBoost) · versão 1

> Gerado automaticamente pelo estágio `train` (run `20261023T015952-840480`, MLflow `7bfcc35a171a46d08252fbf7cf01a92b`).

## Uso pretendido
Estimar a probabilidade de inadimplência (PD) de propostas de crédito pessoal para apoiar a decisão
de concessão. **Não** deve ser usado para marketing, precificação discriminatória ou decisões sem
possibilidade de revisão humana (LGPD art. 20).

## Dados
- Fonte: Home Credit Default Risk (`application_train.csv`), projeção mínima de 17 features.
- Referência pseudonimizada (HMAC-SHA256); atributos sensíveis (gênero, estado civil) **excluídos** das features.
- Hashes dos conjuntos: train `c50e0b9a0aa72415…`, valid `60b9201a872684bc…`, test `4e5ac35866c963a0…`.

## Desempenho (referência)
| Conjunto | AUC | KS | Brier | Taxa de aprovação | Bad rate aprovados |
|---|---|---|---|---|---|
| Validação | 0.7525 | 0.3784 | 0.06826 | 94.2% | 6.59% |
| Teste | 0.7397 | 0.3588 | 0.06894 | 94.3% | 6.77% |

Limiar de decisão por custo esperado (LGD=0.45, margem=0.1): **PD < 0.22** → aprova.

## Features mais importantes (ganho)
- `education`: 21.0%
- `income_type`: 17.2%
- `ext_source_3`: 10.8%
- `contract_type`: 10.6%
- `ext_source_2`: 10.6%
- `has_employment`: 3.3%
- `goods_price`: 3.2%
- `age_years`: 3.1%

## Fairness (conjunto de teste)
| Atributo | Disparate impact (≥ 0,80) | Gap equal opportunity | Gap AUC | Grupo menos aprovado |
|---|---|---|---|---|
| gender | 0.972 | 0.019 | 0.008 | M |
| age_band | 0.887 | 0.092 | 0.087 | 18-25 |

## Limitações
- Treinado em dados históricos de outro mercado: requer monitoramento contínuo de drift (ver dashboard).
- Scores de bureau externos têm ausência informativa (não-MCAR); o modelo trata nulos nativamente.
- Idade é usada como feature (prática comum em crédito), mas é monitorada por faixa etária na auditoria de vieses.
