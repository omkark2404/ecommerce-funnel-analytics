.PHONY: notebook lint lint-sql lint-python test up validate down

PYTHON ?= python
COMPOSE ?= docker compose

notebook:
	$(PYTHON) -m nbconvert --to notebook --execute --inplace notebooks/funnel_analysis.ipynb

lint: lint-sql lint-python

lint-sql:
	$(PYTHON) -m sqlfluff lint sql/

lint-python:
	$(PYTHON) -m ruff check src tests scripts

test:
	PYTHONPATH=. $(PYTHON) -m pytest tests/ -v

up:
	$(COMPOSE) up -d --wait mysql

validate:
	$(PYTHON) scripts/validate_results.py --mode docker

down:
	$(COMPOSE) down
