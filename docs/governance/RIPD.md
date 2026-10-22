# Relatório de Impacto à Proteção de Dados (LGPD art. 38)

## 1. Descrição do tratamento
Scoring automatizado de propostas de crédito com monitoramento contínuo de qualidade de dados, drift,
desempenho e vieses. Dados descritos no [inventário de PII](PII_INVENTORY.md).

## 2. Necessidade e proporcionalidade
- **Minimização:** apenas 18 colunas das 122 disponíveis na fonte são lidas (`usecols`).
- **Finalidade restrita:** o contrato de dados veda marketing e perfilamento comercial (`terms.limitations`).
- **Atributos de risco discriminatório** (gênero, estado civil) não alimentam o modelo.

## 3. Matriz de riscos

| # | Risco ao titular | Prob. | Impacto | Medidas | Risco residual |
|---|---|---|---|---|---|
| R1 | Reidentificação a partir de dados do modelo | Média | Alto | HMAC com salt secreto; relatórios só com agregados e faixas; supressão de grupos com n < 30 | Baixo |
| R2 | Vazamento de dados brutos | Baixa | Alto | Dados fora do Git (`.gitignore`); varredura de PII em logs/relatórios; quarentena com retenção curta | Baixo |
| R3 | Discriminação algorítmica (gênero) | Média | Alto | Atributo fora do modelo; regra dos 4/5 como guardrail de promoção; monitoramento por janela | Baixo |
| R4 | Discriminação por idade | Média | Médio | Idade é fator de risco legítimo, mas monitorada por faixa (*equal opportunity*); revisão periódica | Médio |
| R5 | Decisão errada por degradação silenciosa (drift) | Alta | Alto | Contratos, detecção de drift, alertas P1 e política de resposta com guardrails | Baixo |
| R6 | Decisão automatizada sem explicação | Média | Médio | Motivos da decisão (top-3 contribuições) e fluxo de revisão humana (art. 20) | Baixo |
| R7 | Uso para finalidade incompatível | Baixa | Médio | Termos de uso no contrato de dados; controle de acesso por papel | Baixo |

## 4. Direitos do titular
- **Acesso, correção e eliminação:** a área autorizada usa o salt (cofre) para localizar o pseudônimo.
- **Revisão de decisão automatizada (art. 20):** os motivos da decisão são gerados por proposta (`reports/model/decision_reasons_sample.csv`), com revisão humana mediante solicitação.
- **Informação sobre critérios:** o [model card](../../reports/model/MODEL_CARD.md) descreve uso pretendido, dados, desempenho e limitações.

## 5. Conclusão
Com as medidas implementadas, o risco residual é **baixo**, exceto R4, que é **médio** e tem
revisão trimestral com Risco e Compliance.
