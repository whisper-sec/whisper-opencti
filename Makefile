.PHONY: help dev-up dev-down dev-restart dev-logs dev-status dev-clean qa-up qa-down qa-restart qa-logs qa-status qa-clean test lint

ENV_FILE   ?= .env.dev
BASE_FILE  := docker-compose.base.yml

# Layered env loading: .env.dev provides committed defaults, an optional
# local .env (gitignored) overrides individual vars on top. Modern docker
# compose merges multiple --env-file flags, last wins. Drop secrets like
# WHISPER_API_KEY into .env and they apply to both dev and qa stacks
# without ever touching .env.dev.
ENV_FILES := --env-file $(ENV_FILE)
ifneq ($(wildcard .env),)
  ENV_FILES += --env-file .env
endif

COMPOSE_DEV := docker compose -p whisper-opencti-dev $(ENV_FILES) -f $(BASE_FILE) -f docker-compose.dev.yml
COMPOSE_QA  := docker compose -p whisper-opencti-qa  $(ENV_FILES) -f $(BASE_FILE) -f docker-compose.qa.yml

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

dev-up: ## Bring up the local dev stack (OpenCTI + deps + connector built from source)
	$(COMPOSE_DEV) up -d --build
	@echo ""
	@echo "OpenCTI will be available at $$(grep OPENCTI_BASE_URL $(ENV_FILE) | cut -d= -f2)"
	@echo "Login: $$(grep OPENCTI_ADMIN_EMAIL $(ENV_FILE) | cut -d= -f2) / $$(grep OPENCTI_ADMIN_PASSWORD $(ENV_FILE) | cut -d= -f2)"
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

qa-up: ## Bring up the QA stack (validates the published GHCR image end-to-end)
	$(COMPOSE_QA) up -d
	@echo ""
	@echo "QA stack: connector image = $${WHISPER_CONNECTOR_IMAGE:-ghcr.io/whisper-sec/whisper-opencti:v0.1.0-rc2}"
	@echo "OpenCTI will be available at $$(grep OPENCTI_BASE_URL $(ENV_FILE) | cut -d= -f2)"
	@echo "Login: $$(grep OPENCTI_ADMIN_EMAIL $(ENV_FILE) | cut -d= -f2) / $$(grep OPENCTI_ADMIN_PASSWORD $(ENV_FILE) | cut -d= -f2)"
	@echo "Note: cannot run alongside dev stack — both bind the same OPENCTI_PORT. Run \`make dev-down\` first if needed."

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

lint: ## Run ruff lint + format check
	ruff check src/ tests/
	ruff format --check src/ tests/