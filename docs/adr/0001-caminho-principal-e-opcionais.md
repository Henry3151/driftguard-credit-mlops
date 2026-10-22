# ADR-0001: Caminho principal autossuficiente; componentes opcionais desacoplados

- **Status:** Aceito
- **Data:** 2026-10-22

## Contexto

Qualquer pessoa do time (ou auditoria) precisa reproduzir as evidências rapidamente, em qualquer máquina. Ao mesmo tempo, um sistema de produção maduro usaria orquestrador, tracing distribuído e uma stack de métricas com servidor. Esses componentes exigem Docker, portas e serviços, o que aumenta muito a chance de "não rodou na minha máquina".

## Decisão

Dividir o projeto em duas camadas:

- **Caminho principal:** roda com `make all`, só com Python e as bibliotecas centrais do projeto, MLflow local (SQLite), `prometheus-client` gravando arquivo `.prom` e dashboard HTML estático.
- **Opcionais** (Airflow, OpenTelemetry, Prometheus/Pushgateway/Grafana, NannyML): ficam em `src/driftguard/adapters/`, `orchestration/` e `observability/`, ativados por flag de ambiente, extra do `pip` ou profile do Docker Compose.

O núcleo nunca importa as opcionais diretamente. O adaptador verifica a lib e a flag; se faltar algo, registra `OPTIONAL_COMPONENT_SKIPPED` e segue. O DAG do Airflow só chama a mesma CLI, sem lógica duplicada.

## Alternativas consideradas

- **Alternativa 1:** tudo em Docker Compose obrigatório. Rejeitada: aumenta o atrito de onboarding e o risco de falha no ambiente.
- **Alternativa 2:** só o caminho principal. Rejeitada: perde a demonstração de maturidade de produção.

## Consequências

- (+) Evidências reproduzíveis em um comando; o CI prova a independência (job `core-clean-env`).
- (+) A maturidade de produção fica demonstrada sem virar pré-requisito.
- (−) Há duas formas de visualizar métricas (HTML e Grafana) para manter.
