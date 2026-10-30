PY ?= python

.PHONY: install test evals run docker-up docker-test ingest

install:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest -q

evals:
	$(PY) evals/run_evals.py

run:
	$(PY) -m uvicorn getnet_agents.api:app --reload --app-dir src

ingest:
	$(PY) -m getnet_agents.rag.ingest --stats

docker-up:
	docker compose up -d --build

docker-test:
	docker compose --profile test run --rm test

smoke:
	$(PY) scripts/smoke_cases.py evals/dataset.jsonl smoke_results.json
