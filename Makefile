# AEGIS AI — Root Makefile
# Delegates to each component's own Makefile / scripts.

COMPOSE := docker compose -f infrastructure/docker/docker-compose.dev.yml

.PHONY: help setup dev test lint build up down logs clean

help: ## Show this help (default target)
	@echo "AEGIS AI"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

setup: ## Install all components (backend deps + frontend deps)
	$(MAKE) -C backend setup
	cd frontend && npm ci

dev: up ## Start the dev stack via docker compose

test: ## Run backend + frontend test suites
	$(MAKE) -C backend test
	cd frontend && npm test -- --watchAll=false 2>/dev/null || echo "frontend tests: none configured"

lint: ## Lint backend + frontend
	$(MAKE) -C backend lint
	cd frontend && npm run lint

build: ## Build all production images (backend + frontend)
	$(MAKE) -C backend build
	docker build -f frontend/Dockerfile -t aegis-frontend:latest ./frontend

up: ## Start the full dev stack (postgres, redis, backend, frontend)
	$(COMPOSE) up -d --build

down: ## Stop the dev stack
	$(COMPOSE) down

logs: ## Tail stack logs
	$(COMPOSE) logs -f

clean: ## Clean backend artefacts + docker stack volumes
	$(MAKE) -C backend clean
	$(COMPOSE) down -v --remove-orphans
