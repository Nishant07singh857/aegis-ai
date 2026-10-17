# AEGIS AI — Backend Makefile
# All commands run from this directory (backend/).

.PHONY: setup dev test lint lint-fix migrate migrate-new build clean help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

setup: ## Install dependencies with Poetry
	poetry install

dev: ## Run the FastAPI dev server with auto-reload
	poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test: ## Run the full backend test suite
	poetry run pytest tests/ -q

lint: ## Check lint + formatting (fails on violations)
	poetry run ruff check app tests
	poetry run black --check app tests

lint-fix: ## Auto-fix lint + formatting issues
	poetry run ruff check --fix app tests
	poetry run black app tests

migrate: ## Apply alembic migrations up to head
	poetry run alembic upgrade head

migrate-new: ## Create a new autogenerate migration: make migrate-new msg="add users table"
	poetry run alembic revision --autogenerate -m "$(msg)"

build: ## Build the production backend Docker image
	docker build -f Dockerfile -t aegis-backend:latest .

clean: ## Remove caches, build artefacts and venv
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .venv .pytest_cache .ruff_cache htmlcov .coverage
