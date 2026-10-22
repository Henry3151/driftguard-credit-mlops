# ADR-0003: Contrato YAML como fonte da verdade; schema Pandera gerado

- **Status:** Aceito
- **Data:** 2026-10-22

## Contexto

Contratos de dados são acordos entre produtor e consumidor, não só código. Precisam de dono, versão, SLAs, termos de uso e política de compatibilidade, e devem ser legíveis por pessoas não técnicas (risco, compliance).

## Decisão

Manter `contracts/credit_application.v1.yaml` no formato da Data Contract Specification como **única fonte da verdade**. Um *loader* gera o schema Pandera a partir do YAML, então não existem duas definições divergentes. Mudanças que quebram exigem versão nova (`v2`) e respeitam o `noticePeriod`; a compatibilidade padrão é `backward`.

## Alternativas consideradas

- **Schema só em Python:** rejeitado, porque não é legível pelo negócio e não carrega SLAs e termos.
- **YAML e Pandera escritos à mão separadamente:** rejeitado pelo risco de divergência.

## Consequências

- (+) Um artefato de governança e um de engenharia ao mesmo tempo.
- (−) O loader precisa de testes próprios (cobertos em `tests/contract`).
