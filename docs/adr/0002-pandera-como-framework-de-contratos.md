# ADR-0002: Pandera como framework de contratos de dados

- **Status:** Aceito
- **Data:** 2026-10-22

## Contexto

Os candidatos naturais eram Great Expectations e Pandera. O pipeline trabalha com DataFrames pandas de tamanho moderado, e as regras precisam ser testáveis em `pytest` e versionadas junto com o código.

## Decisão

Usar **Pandera** (`DataFrameModel`/`DataFrameSchema`):

- `lazy=True` coleta todas as violações de uma vez, com `failure_cases` por linha, que alimentam a quarentena;
- o decorator `@pa.check_types` protege a entrada e a saída das funções de transformação (Gate 2).

## Alternativas consideradas

- **Great Expectations 1.x:** mais de 300 expectations prontas e Data Docs. Rejeitado como principal porque traz mais configuração (contexto, data source, suite) para pouco ganho neste escopo, e é mais lento em pandas.
- **Deequ:** exige Spark. Desnecessário para o volume do projeto.

## Consequências

- (+) Schema como código, leve, rápido e testável.
- (−) Sem Data Docs nativos. Compensado pelo relatório JSON e pela seção de validação no dashboard.
