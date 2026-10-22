.PHONY: setup lint test test-e2e all demo ingest bad-batch train simulate monitor retrain causal pii-scan dashboard figures mlflow observability clean

PY ?= python

setup:            ## instala dependências e o pacote em modo editável
	$(PY) -m pip install -r requirements-dev.txt && $(PY) -m pip install -e .

lint:             ## análise estática
	ruff check src tests && ruff format --check src tests

test:             ## testes rápidos (unitários, contrato, privacidade, simulação)
	$(PY) -m pytest -m "not e2e"

test-e2e:         ## teste ponta a ponta com dados sintéticos
	$(PY) -m pytest -m e2e

all:              ## pipeline completo com o dataset real
	$(PY) -m driftguard all

demo:             ## pipeline completo com dados sintéticos (sem baixar o dataset)
	$(PY) -m driftguard demo

ingest bad-batch train simulate monitor retrain causal pii-scan dashboard figures:
	$(PY) -m driftguard $@

mlflow:           ## interface do MLflow em http://localhost:5000
	mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5000

observability:    ## stack opcional Prometheus + Pushgateway + Grafana
	docker compose -f observability/docker-compose.yml --profile observability up -d

clean:            ## remove artefatos de execução (mantém configs e código)
	rm -rf data/reference data/production data/audit data/quarantine data/bad_batch.csv models logs mlflow.db mlartifacts
