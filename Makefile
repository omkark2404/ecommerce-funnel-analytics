.PHONY: notebook lint up validate down

PYTHON ?= python
COMPOSE ?= docker compose

notebook:
	$(PYTHON) -m nbconvert --to notebook --execute --inplace notebooks/funnel_analysis.ipynb

lint:
	$(PYTHON) -m sqlfluff lint sql/

up:
	$(COMPOSE) up -d --wait mysql

validate:
	$(PYTHON) scripts/validate_results.py --mode docker

down:
	$(COMPOSE) down
