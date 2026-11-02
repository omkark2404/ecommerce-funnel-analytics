.PHONY: notebook lint lint-sql lint-python format check test up validate down reset figures

PYTHON ?= python
COMPOSE ?= docker compose

notebook:
	$(PYTHON) -m nbconvert --to notebook --execute --inplace notebooks/funnel_analysis.ipynb

lint: lint-sql lint-python

lint-sql:
	$(PYTHON) -m sqlfluff lint sql/

lint-python:
	$(PYTHON) -m ruff check src tests scripts

format:
	$(PYTHON) -m ruff format src tests scripts

check: lint test
	$(PYTHON) -m ruff format --check src tests scripts
	$(PYTHON) scripts/check_figures.py

figures:
	$(PYTHON) scripts/check_figures.py

test:
	PYTHONPATH=. $(PYTHON) -m pytest tests/ -v

up:
	$(COMPOSE) up -d --wait mysql

validate:
	$(PYTHON) scripts/validate_results.py --mode docker

down:
	$(COMPOSE) down

# Stop the database and delete its data volume, so the next `make up`
# starts from a clean schema. Only for the local demo database.
reset:
	$(COMPOSE) down -v
