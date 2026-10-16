.PHONY: help install dev-install test lint format clean dvc-run dvc-repro dvc-push dvc-pull docker-build docker-up docker-down k8s-deploy k8s-undeploy airflow-test stack stack-stop tls load-test

## Show this help message
help:
	@echo "AeroPredict Platform - Available commands:"
	@echo ""
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## //' | column -t -s ':'

## Install production dependencies
install:
	pip install -r requirements.txt

## Install development dependencies
dev-install:
	pip install -r requirements.txt
	pip install pytest pytest-cov ruff mypy pre-commit

## Run tests
test:
	python -m pytest tests/ -v --tb=short --maxfail=3

## Run tests with coverage
test-cov:
	python -m pytest tests/ -v --cov=src --cov-report=term-missing --cov-report=xml

## Lint with ruff
lint:
	ruff check src/ tests/

## Format code with ruff
format:
	ruff format src/ tests/

## Clean up generated files
clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

## Run DVC pipeline (full)
dvc-run:
	dvc repro

## Reproduce DVC pipeline from scratch
dvc-repro:
	dvc repro --force

## Push data to DVC remote
dvc-push:
	dvc push

## Pull data from DVC remote
dvc-pull:
	dvc pull

## Build Docker image
docker-build:
	docker build -t aeropredict/api:latest -f Dockerfile .

## Start all services with Docker Compose
docker-up:
	docker compose up -d

## Stop all services
docker-down:
	docker compose down

## Deploy to Kubernetes
k8s-deploy:
	helm install aeropredict ./helm-chart

## Undeploy from Kubernetes
k8s-undeploy:
	helm uninstall aeropredict

## Test Airflow DAG import
airflow-test:
	python -c "from dags.training_pipeline import dag; print('DAG OK:', dag.dag_id)"

## Start the live stack (API, MLflow, MinIO, Prometheus, Grafana)
stack:
	./run_stack.sh

## Stop the live stack and its containers
stack-stop:
	@-pkill -f "run_api.py" 2>/dev/null || true
	@-docker rm -f prom_aeropredict graf_aeropredict aeropredict_minio 2>/dev/null || true
	@echo "stopped"

## Start the HTTPS reverse proxy (self-signed, port 8443)
tls:
	./infrastructure/tls/start_tls_proxy.sh

## Run the load test against the live stack
load-test:
	python3 load_test.py --requests 60 --concurrency 6
