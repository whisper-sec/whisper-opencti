.PHONY: help dev-up dev-down dev-restart dev-logs dev-status dev-clean

ENV_FILE ?= .env.dev
COMPOSE  := docker compose --env-file $(ENV_FILE) -f docker-compose.dev.yml

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

dev-up: ## Bring up the local dev stack (OpenCTI + deps + connector)
	$(COMPOSE) up -d --build
	@echo ""
	@echo "OpenCTI will be available at $$(grep OPENCTI_BASE_URL $(ENV_FILE) | cut -d= -f2)"
	@echo "Login: $$(grep OPENCTI_ADMIN_EMAIL $(ENV_FILE) | cut -d= -f2) / $$(grep OPENCTI_ADMIN_PASSWORD $(ENV_FILE) | cut -d= -f2)"
	@echo "First-time startup can take 2-3 minutes while ES initializes."

dev-down: ## Stop the dev stack (preserves volumes)
	$(COMPOSE) down

dev-restart: ## Rebuild and restart the connector container only
	$(COMPOSE) up -d --build connector-whisper

dev-logs: ## Tail logs from the dev stack
	$(COMPOSE) logs -f

dev-status: ## Show status of dev stack services
	$(COMPOSE) ps

dev-clean: ## Stop the dev stack AND remove volumes (fresh state)
	$(COMPOSE) down -v
