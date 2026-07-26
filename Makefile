# AeroTwin developer entrypoint.
# Run `make` or `make help` for the command list.

.DEFAULT_GOAL := help
SHELL := /bin/bash
VENV  := .venv
PY    := $(VENV)/bin/python
PIP   := $(VENV)/bin/pip
COMPOSE_DEV := docker compose -f docker-compose.dev.yml

PY_PKGS := -p at_core -p at_config -p at_observability -p at_api
EDITABLE := -e libs/at_core -e libs/at_config -e libs/at_observability -e services/api

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
		ruff mypy import-linter pyyaml
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
