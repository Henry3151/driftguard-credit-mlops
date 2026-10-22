# ADR-0004: Circuit breaker de 5% e quarentena por linha

- **Status:** Aceito
- **Data:** 2026-10-22

## Contexto

Bloquear sempre o lote inteiro por uma única linha ruim derruba a disponibilidade. Deixar passar linhas ruins contamina o modelo (data cascades). Falhas silenciosas são o pior cenário.

## Decisão

- Violação de **schema/volume**, ou linhas inválidas **acima de 5%** do lote: o **lote inteiro** é bloqueado (exit code 2, alerta P1, `INGESTION_BLOCKED`).
- Linhas inválidas **até 5%**: só elas vão para `data/quarantine/<run_id>/` com `reason_code` (dead-letter). O restante segue, com alerta P2.
- O limite de 5% fica em `configs/slo.yaml`.

## Alternativas consideradas

- **Tudo-ou-nada:** mais simples, mas frágil em produção.
- **Descartar linhas ruins silenciosamente:** viola o princípio P1.

## Consequências

- (+) Equilíbrio entre proteção e disponibilidade; tudo fica rastreável para análise posterior.
- (−) O valor de 5% é uma heurística e precisa de revisão com o histórico real.
