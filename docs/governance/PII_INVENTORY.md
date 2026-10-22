# Inventário de dados pessoais

Classificação das colunas usadas pelo projeto (projeção mínima da fonte — as demais ~100 colunas do
dataset original **não são lidas**).

| Coluna canônica | Origem | Classificação | Tratamento | Uso |
|---|---|---|---|---|
| `customer_id` | `SK_ID_CURR` | Identificador direto | **Pseudonimização HMAC-SHA256** com salt secreto (fora do Git) | Chave técnica |
| `gender` | `CODE_GENDER` | **Sensível para fins de não discriminação** (atributo protegido) | **Segregado** em `data/audit/`; nunca é feature | Auditoria de vieses |
| `family_status` | `NAME_FAMILY_STATUS` | Pessoal — risco de discriminação | **Segregado** em `data/audit/`; nunca é feature | Auditoria (não utilizado nas métricas atuais) |
| `age_years` | `DAYS_BIRTH` | Quase-identificador | Convertido em anos; publicado apenas em **faixas** (`age_band`) | Feature de risco + auditoria |
| `income` | `AMT_INCOME_TOTAL` | Pessoal (financeiro) | Sem publicação individual | Feature |
| `credit_amount`, `annuity`, `goods_price` | `AMT_*` | Pessoal (financeiro) | Sem publicação individual | Features |
| `employment_years`, `has_employment` | `DAYS_EMPLOYED` | Pessoal | Sentinela documentada mapeada explicitamente | Features |
| `ext_source_2`, `ext_source_3` | `EXT_SOURCE_*` | Pessoal (score de bureau) | Nulos preservados (ausência informativa) | Features |
| `children_count` | `CNT_CHILDREN` | Pessoal | — | Feature |
| `region_rating` | `REGION_RATING_CLIENT` | Quase-identificador (agregado regional) | — | Feature |
| `contract_type`, `income_type`, `education`, `own_realty` | `NAME_*`, `FLAG_*` | Pessoal | — | Features |
| `target` | `TARGET` | Pessoal (comportamento de pagamento) | — | Rótulo |

**Dados sensíveis (LGPD art. 5º, II):** o conjunto utilizado não contém origem racial, religião,
opinião política, saúde, vida sexual, dados genéticos ou biométricos. Gênero e estado civil não são
"sensíveis" na definição legal, mas são tratados com o mesmo rigor por representarem risco de
discriminação (art. 6º, IX).

**Controles automatizados:**

- `driftguard pii-scan` varre logs e relatórios em busca de IDs originais e do salt;
- a coluna `CPF_CLIENTE` do lote defeituoso de teste prova que um campo pessoal **não contratado** é barrado pelo schema estrito.
