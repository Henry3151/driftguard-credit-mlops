# Dados

Os dados **não são versionados** (licença do provedor e LGPD). Para executar com dados reais:

1. Baixe `application_train.csv` da competição **Home Credit Default Risk** no Kaggle
   (https://www.kaggle.com/competitions/home-credit-default-risk/data).
2. Salve em `data/raw/application_train.csv`.

Sem o arquivo, use `make demo` — o pipeline completo roda com dados sintéticos no mesmo formato.

Estrutura criada pelo pipeline:

| Pasta | Conteúdo |
|---|---|
| `raw/` | Fonte original (somente leitura) |
| `reference/` | Dataset de **Referência** (treino/validação/teste) e pool de produção, pseudonimizados |
| `production/` | Janelas mensais simuladas (onde o drift acontece) + `scenario.json` |
| `audit/` | Atributos sensíveis segregados — uso exclusivo em auditoria de vieses |
| `quarantine/` | Lotes/linhas rejeitados pelo contrato (dead-letter), retenção de 30 dias |
