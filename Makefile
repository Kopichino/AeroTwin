# AeroTwin developer entrypoint.
# Run `make` or `make help` for the command list.

.DEFAULT_GOAL := help
SHELL := /bin/bash
VENV  := .venv
PY    := $(VENV)/bin/python
PIP   := $(VENV)/bin/pip
COMPOSE_DEV := docker compose -f docker-compose.dev.yml

PY_PKGS := -p at_ml -p at_core -p at_config -p at_observability -p at_bus -p at_data -p at_persistence -p at_api -p at_twin
EDITABLE := -e libs/at_core -e libs/at_config -e libs/at_observability \
            -e libs/at_bus -e libs/at_data -e ml -e libs/at_persistence -e services/api -e services/twin_engine

.PHONY: help
help: ## Show this help
	@echo ""
	@echo "  AeroTwin -- Agentic Digital Twin Platform"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ── setup ─────────────────────────────────────────────────────────────────────

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip

.PHONY: install
install: $(VENV)/bin/activate ## Create the venv and install all workspace packages
	$(PIP) install --quiet $(EDITABLE)
	$(PIP) install --quiet pytest pytest-asyncio pytest-cov hypothesis httpx \
		ruff mypy import-linter pyyaml aiosqlite
	@echo "Workspace installed. Activate with: source $(VENV)/bin/activate"

# ── quality gates ─────────────────────────────────────────────────────────────

.PHONY: lint
lint: ## Ruff lint + format check
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

.PHONY: format
format: ## Auto-fix lint issues and format the codebase
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

.PHONY: typecheck
typecheck: ## Mypy in strict mode
	$(VENV)/bin/mypy $(PY_PKGS)

.PHONY: arch
arch: ## Verify architectural layering contracts (import-linter)
	$(VENV)/bin/lint-imports

.PHONY: test
test: ## Run the full test suite
	$(VENV)/bin/pytest

.PHONY: test-core
test-core: ## Domain kernel tests, enforcing 100 percent coverage
	$(VENV)/bin/pytest libs/at_core/tests \
		--cov=at_core --cov-report=term-missing --cov-fail-under=100

.PHONY: cov
cov: ## Full suite with a coverage report
	$(VENV)/bin/pytest --cov=at_core --cov=at_config --cov=at_api \
		--cov-report=term-missing --cov-report=html
	@echo "HTML report: htmlcov/index.html"

.PHONY: check
check: lint typecheck arch test ## Run every gate CI runs (do this before pushing)
	@echo ""
	@echo "  All quality gates passed."

# ── data ──────────────────────────────────────────────────────────────────────

.PHONY: data
data: ## Download C-MAPSS, verify it, and build the Parquet interim layer
	$(PY) -m at_data.acquire --dest data/raw/cmapss
	$(PY) -c "from pathlib import Path; from at_data.parse import convert_all; \
		print('Converting to Parquet:'); convert_all(Path('data/raw/cmapss'), Path('data/interim'))"
	$(PY) -c "from pathlib import Path; from at_data.parse import load_parquet; \
		from at_data.regimes import fit_regimes, save_models; from at_core.domain.enums import Subset; \
		m={s: fit_regimes(load_parquet(s,'train',Path('data/interim')), s) for s in Subset}; \
		save_models(m, Path('data/processed/regimes.json')); \
		print('Regime models:', {k.value: f'{v.n_regimes} regimes, silhouette {v.silhouette:.4f}' for k,v in m.items()})"

.PHONY: data-verify
data-verify: ## Verify the dataset on disk without downloading
	$(PY) -m at_data.acquire --verify-only --dest data/raw/cmapss

.PHONY: eda
eda: ## Regenerate docs/reports/eda.md from the interim layer
	$(PY) -c "from pathlib import Path; from at_data.eda import build_report; \
		print('wrote', build_report(Path('data/interim'), Path('docs/reports/eda.md')))"

.PHONY: demo
demo: ## Run the full live platform: API + twins + dashboard on :8000
	@echo ""
	@echo "  Dashboard  http://localhost:8000/dashboard"
	@echo "  API docs   http://localhost:8000/docs"
	@echo ""
	$(VENV)/bin/uvicorn at_api.main:app --host 0.0.0.0 --port 8000

.PHONY: monitor
monitor: ## Live terminal fleet monitor (M3 twin engine demo)
	$(PY) -m at_twin.monitor --subset FD002 --speed 8 --duration 60

.PHONY: train
train: ## Train and compare every RUL architecture, then register the winner
	$(PY) -c "from pathlib import Path; from at_core.domain.enums import Subset; \
		from at_ml.compare import run_comparison, build_report, save_results; \
		runs=[run_comparison(Subset.FD001, Path('data/interim'), epochs=30)]; \
		build_report(runs, Path('docs/reports/model-comparison.md')); \
		save_results(runs, Path('models/comparison.json'))"

# ── api ───────────────────────────────────────────────────────────────────────

.PHONY: openapi
openapi: ## Regenerate docs/api/openapi.json
	$(PY) tools/codegen/export_openapi.py

.PHONY: openapi-check
openapi-check: ## Fail if the committed OpenAPI schema is stale
	$(PY) tools/codegen/export_openapi.py --check

.PHONY: api
api: ## Run the API locally with hot reload
	$(VENV)/bin/uvicorn at_api.main:app --reload --port 8000

# ── docker ────────────────────────────────────────────────────────────────────

.PHONY: dev
dev: ## Start the development stack (postgres, redis, chroma, api, jaeger)
	$(COMPOSE_DEV) up --build

.PHONY: dev-detach
dev-detach: ## Start the development stack in the background
	$(COMPOSE_DEV) up --build -d

.PHONY: down
down: ## Stop the stack (volumes preserved)
	$(COMPOSE_DEV) down

.PHONY: clean-volumes
clean-volumes: ## Stop the stack and delete all data volumes
	$(COMPOSE_DEV) down -v

.PHONY: logs
logs: ## Tail logs from every service
	$(COMPOSE_DEV) logs -f

.PHONY: compose-validate
compose-validate: ## Validate compose file syntax
	$(COMPOSE_DEV) config --quiet && echo "compose files are valid"

# ── housekeeping ──────────────────────────────────────────────────────────────

.PHONY: clean
clean: ## Remove caches and build artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
	@echo "Clean."
