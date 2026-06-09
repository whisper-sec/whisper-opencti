.PHONY: help dev-up dev-down dev-restart dev-logs dev-status dev-clean qa-up qa-down qa-restart qa-logs qa-status qa-clean test lint _check-env

BASE_FILE := docker-compose.base.yml

# Single env file (.env, gitignored). `.env.example` is the committed
# template - `cp .env.example .env` is the required first step on a
# fresh clone.
COMPOSE_DEV := docker compose -p whisper-opencti-dev --env-file .env -f $(BASE_FILE) -f docker-compose.dev.yml
COMPOSE_QA  := docker compose -p whisper-opencti-qa  --env-file .env -f $(BASE_FILE) -f docker-compose.qa.yml

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

_check-env:
	@if [ ! -f .env ]; then \
	  echo "Error: .env not found."; \
	  echo "Run:  cp .env.example .env"; \
	  echo "Then edit .env to set WHISPER_API_KEY."; \
	  exit 1; \
	fi

dev-up: _check-env ## Bring up the local dev stack (OpenCTI + deps + connector built from source)
	$(COMPOSE_DEV) up -d --build
	@echo ""
	@echo "OpenCTI will be available at $$(grep OPENCTI_BASE_URL .env | cut -d= -f2)"
	@echo "Login: $$(grep OPENCTI_ADMIN_EMAIL .env | cut -d= -f2) / $$(grep OPENCTI_ADMIN_PASSWORD .env | cut -d= -f2)"
	@echo "First-time startup can take 2-3 minutes while ES initializes."

dev-down: ## Stop the dev stack (preserves volumes)
	$(COMPOSE_DEV) down

dev-restart: ## Rebuild and restart the connector container only
	$(COMPOSE_DEV) up -d --build connector-whisper

dev-logs: ## Tail logs from the dev stack
	$(COMPOSE_DEV) logs -f

dev-status: ## Show status of dev stack services
	$(COMPOSE_DEV) ps

dev-clean: ## Stop the dev stack AND remove volumes (fresh state)
	$(COMPOSE_DEV) down -v

qa-up: _check-env ## Bring up the QA stack (validates the published GHCR image end-to-end)
	$(COMPOSE_QA) up -d
	@echo ""
	@echo "QA stack: connector version = $$(grep '^WHISPER_CONNECTOR_VERSION=' .env | cut -d= -f2)"
	@echo "OpenCTI will be available at $$(grep OPENCTI_BASE_URL .env | cut -d= -f2)"
	@echo "Login: $$(grep OPENCTI_ADMIN_EMAIL .env | cut -d= -f2) / $$(grep OPENCTI_ADMIN_PASSWORD .env | cut -d= -f2)"
	@echo "Note: cannot run alongside dev stack - both bind the same OPENCTI_PORT. Run \`make dev-down\` first if needed."

qa-down: ## Stop the QA stack (preserves volumes)
	$(COMPOSE_QA) down

qa-restart: ## Pull the latest connector image and restart just the connector
	$(COMPOSE_QA) pull connector-whisper
	$(COMPOSE_QA) up -d connector-whisper

qa-logs: ## Tail logs from the QA stack
	$(COMPOSE_QA) logs -f

qa-status: ## Show status of QA stack services
	$(COMPOSE_QA) ps

qa-clean: ## Stop the QA stack AND remove volumes (fresh state)
	$(COMPOSE_QA) down -v

test: ## Run unit tests (assumes `pip install -r requirements.txt -r requirements-dev.txt`)
	pytest

# Lint + format toolchain matches OpenCTI-Platform/connectors upstream
# (see shared/pylint_plugins/check_stix_plugin/ for the vendored STIX-ID
# generator plugin). The pylint check set mirrors upstream's lint.yml -
# `--disable=all --enable=no_generated_id_stix,no-value-for-parameter,unused-import`
# - not a full style sweep.
lint: ## Format check (isort + black) + flake8 + narrow pylint (vendored STIX-ID plugin)
	isort --profile black --line-length 88 --check-only --diff .
	black --check --diff .
	flake8 --ignore=E,W .
	cd shared/pylint_plugins/check_stix_plugin && \
	  PYTHONPATH=. pylint ../../../src ../../../tests \
	    --disable=all \
	    --enable=no_generated_id_stix,no-value-for-parameter,unused-import \
	    --load-plugins linter_stix_id_generator

format: ## Apply isort + black formatting in place
	isort --profile black --line-length 88 .
	black .