# CASS platform developer commands.
#
# Section 12 requires that a fresh environment be buildable from
# version-controlled configuration. These targets are that path. The CI
# workflow runs the same commands directly on Linux rather than through this
# file, so a change here is only proven by running it on a clean machine.

# The recipes are POSIX shell. Native Windows make falls back to cmd.exe when
# it finds no sh.exe on PATH, and a stock Git for Windows install does not put
# one there, so point it at the sh.exe Git ships. git --exec-path is
# <git>/<mingw64|clangarm64>/libexec/git-core, three levels below it.
ifeq ($(OS),Windows_NT)
GIT_EXEC_PATH := $(shell git --exec-path)
ifneq (,$(GIT_EXEC_PATH))
SHELL := $(GIT_EXEC_PATH)/../../../bin/sh.exe
endif
PYTHON ?= python
VENV_BIN := Scripts
else
SHELL := /bin/bash
PYTHON ?= python3
VENV_BIN := bin
endif

ifneq (posix,$(shell test -d . && echo posix))
$(error These targets need a POSIX shell. On Windows install Git for Windows, or run make from WSL)
endif

.DEFAULT_GOAL := help

BACKEND := backend
FRONTEND := frontend
COMPOSE := docker compose -f deploy/docker-compose.yml --env-file .env
VENV := $(BACKEND)/.venv
# The interpreter is chosen by platform, not by looking for it: this file is
# read before setup-backend creates the virtualenv, so on a clean machine
# there is nothing yet to find.
VENV_PY := .venv/$(VENV_BIN)/python
PY := $(BACKEND)/$(VENV_PY)

.PHONY: help
help: ## Show the available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# -- setup ------------------------------------------------------------------

.PHONY: setup
setup: setup-backend setup-frontend env ## Prepare a clean machine for development

.PHONY: setup-backend
setup-backend: ## Create the backend virtualenv and install every package
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r $(BACKEND)/requirements-dev.txt
	$(PY) -m pip install -e $(BACKEND)/packages/cass_core \
		-e $(BACKEND)/packages/cass_oed \
		-e $(BACKEND)/packages/cass_extract \
		-e $(BACKEND)/services/keys \
		-e "$(BACKEND)/services/converter[hdf5]"

.PHONY: setup-frontend
setup-frontend: ## Install frontend dependencies from the lockfile
	cd $(FRONTEND) && npm ci

.PHONY: env
env: ## Create .env from the template if it does not exist
	@if [ ! -f .env ]; then \
		cp deploy/.env.example .env; \
		echo "Created platform/.env - fill in CASS_SECRET_KEY, POSTGRES_PASSWORD,"; \
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
	cd $(BACKEND) && $(VENV_PY) -m pytest -q

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
	cd $(BACKEND)/cass_api && \
		DJANGO_SETTINGS_MODULE=cass.settings.test ../$(VENV_PY) manage.py \
		spectacular --fail-on-warn --file ../../docs/openapi.yaml

# -- running ----------------------------------------------------------------

# The API applies migrations when it starts, and both targets return only once
# it is serving, so a seed or a first request never meets an unmigrated schema.
#
# The scheduler is part of the control plane: it runs the API's image, so an up
# that rebuilt the image without recreating it would leave it on the old code.
#
# up-engines cannot pass --wait to its own up. The keys and converter images
# prove their package imports and exit 0, which --wait reports as a failure, so
# it starts everything and then waits on the control plane alone.
CONTROL_PLANE := api worker beat

.PHONY: up
up: ## Start the control plane (no engines), once migrated and serving
	$(COMPOSE) up -d --build --wait postgres redis minio minio-init $(CONTROL_PLANE) frontend

.PHONY: up-engines
up-engines: ## Start everything, including OpenQuake and Oasis
	$(COMPOSE) --profile engines up -d --build
	$(COMPOSE) up -d --wait $(CONTROL_PLANE)

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
migrate: ## Apply migrations now; the API also applies them whenever it starts
	$(COMPOSE) run --rm api python manage.py migrate

# Both need a migrated schema, which the API provides by starting.
.PHONY: superuser
superuser: ## Create a platform administrator
	$(COMPOSE) up -d --wait api
	$(COMPOSE) run --rm api python manage.py createsuperuser

.PHONY: seed
seed: ## Load the demonstration project, model version and PiWind portfolio
	$(COMPOSE) up -d --wait api
	$(COMPOSE) run --rm api python manage.py seed_demo

.PHONY: dev-api
dev-api: ## Run the API against a local database, outside Docker
	cd $(BACKEND)/cass_api && \
		DJANGO_SETTINGS_MODULE=cass.settings.dev ../$(VENV_PY) manage.py runserver

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
