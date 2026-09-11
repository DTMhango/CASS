# KRE platform developer commands.
#
# Section 12 requires that a fresh environment be buildable from
# version-controlled configuration. These targets are that path, and the CI
# workflow runs the same ones, so "works on my machine" and "passes in CI"
# cannot drift apart.

SHELL := /bin/bash
.DEFAULT_GOAL := help

BACKEND := backend
FRONTEND := frontend
COMPOSE := docker compose -f deploy/docker-compose.yml --env-file .env
VENV := $(BACKEND)/.venv
PY := $(VENV)/Scripts/python.exe
ifeq (,$(wildcard $(VENV)/Scripts/python.exe))
PY := $(VENV)/bin/python
endif

.PHONY: help
help: ## Show the available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# -- setup ------------------------------------------------------------------

.PHONY: setup
setup: setup-backend setup-frontend env ## Prepare a clean machine for development

.PHONY: setup-backend
setup-backend: ## Create the backend virtualenv and install every package
	python -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r $(BACKEND)/requirements-dev.txt
	$(PY) -m pip install -e $(BACKEND)/packages/kre_core \
		-e $(BACKEND)/packages/kre_oed \
		-e $(BACKEND)/services/keys \
		-e $(BACKEND)/services/converter

.PHONY: setup-frontend
setup-frontend: ## Install frontend dependencies from the lockfile
	cd $(FRONTEND) && npm ci

.PHONY: env
env: ## Create .env from the template if it does not exist
	@if [ ! -f .env ]; then \
		cp deploy/.env.example .env; \
		echo "Created platform/.env - fill in KRE_SECRET_KEY, POSTGRES_PASSWORD,"; \
		echo "MINIO_ROOT_PASSWORD and OASIS_ADMIN_PASS before starting the stack."; \
	else \
		echo "platform/.env already exists; leaving it alone."; \
	fi

# -- quality ----------------------------------------------------------------

.PHONY: check
check: lint typecheck test ## Everything CI runs

.PHONY: test
test: test-backend test-frontend ## Run both test suites

.PHONY: test-backend
test-backend: ## Backend unit, package and API tests
	cd $(BACKEND) && .venv/Scripts/python.exe -m pytest -q 2>/dev/null \
		|| cd $(BACKEND) && .venv/bin/python -m pytest -q

.PHONY: test-frontend
test-frontend: ## Frontend component and page tests
	cd $(FRONTEND) && npm run test

.PHONY: lint
lint: ## Ruff and ESLint
	$(PY) -m ruff check $(BACKEND)
	cd $(FRONTEND) && npm run lint

.PHONY: format
format: ## Apply the fixes both linters can make
	$(PY) -m ruff check --fix $(BACKEND)
	$(PY) -m ruff format $(BACKEND)

.PHONY: typecheck
typecheck: ## TypeScript project references
	cd $(FRONTEND) && npm run typecheck

.PHONY: schema
schema: ## Regenerate the OpenAPI schema, failing on any warning
	cd $(BACKEND)/kre_api && \
		DJANGO_SETTINGS_MODULE=kre.settings.test ../.venv/Scripts/python.exe manage.py \
		spectacular --fail-on-warn --file ../../docs/openapi.yaml

# -- running ----------------------------------------------------------------

.PHONY: up
up: ## Start the control plane (no engines)
	$(COMPOSE) up -d --build postgres redis minio minio-init api worker frontend

.PHONY: up-engines
up-engines: ## Start everything, including OpenQuake and Oasis
	$(COMPOSE) --profile engines up -d --build

.PHONY: down
down: ## Stop the stack, keeping volumes
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and delete its data. Destructive.
	$(COMPOSE) down --volumes --remove-orphans

.PHONY: logs
logs: ## Follow the API and worker logs
	$(COMPOSE) logs -f api worker

.PHONY: migrate
migrate: ## Apply database migrations
	$(COMPOSE) run --rm api python manage.py migrate

.PHONY: superuser
superuser: ## Create a platform administrator
	$(COMPOSE) run --rm api python manage.py createsuperuser

.PHONY: seed
seed: ## Load the demonstration project, model version and PiWind portfolio
	$(COMPOSE) run --rm api python manage.py seed_demo

.PHONY: dev-api
dev-api: ## Run the API against a local database, outside Docker
	cd $(BACKEND)/kre_api && \
		DJANGO_SETTINGS_MODULE=kre.settings.dev ../.venv/Scripts/python.exe manage.py runserver

.PHONY: dev-web
dev-web: ## Run the Vite dev server with API proxying
	cd $(FRONTEND) && npm run dev

# -- release ----------------------------------------------------------------

.PHONY: pin-digests
pin-digests: ## Record the current image digests for a governed deployment
	@echo "# Image digests recorded $$(date -u +%Y-%m-%dT%H:%M:%SZ)" > deploy/image-digests.txt
	@for image in $$(grep -oP '(?<=image: ).*' deploy/docker-compose.yml); do \
		digest=$$(docker image inspect "$$image" --format '{{index .RepoDigests 0}}' 2>/dev/null \
			|| echo "not pulled"); \
		echo "$$image $$digest" >> deploy/image-digests.txt; \
	done
	@cat deploy/image-digests.txt
