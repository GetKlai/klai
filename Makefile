# Klai Local Development
# Run `make help` to see available targets.

COMPOSE := docker compose -f docker-compose.dev.yml --env-file .env.dev
BACKEND_DIR := klai-portal/backend
FRONTEND_DIR := klai-portal/frontend

.PHONY: help setup dev-up dev-down dev-reset dev-status dev-logs backend frontend migrate lint check

# ── Help ─────────────────────────────────────────────────────────────────────

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── Setup ────────────────────────────────────────────────────────────────────

setup: ## First-time setup: copy env files, install dependencies
	@echo "==> Copying environment files..."
	@test -f .env.dev || cp .env.dev.example .env.dev
	@test -f $(BACKEND_DIR)/.env || cp $(BACKEND_DIR)/.env.example $(BACKEND_DIR)/.env
	@test -f $(FRONTEND_DIR)/.env.local || cp $(FRONTEND_DIR)/.env.local.example $(FRONTEND_DIR)/.env.local
	@echo ""
	@echo "==> Installing backend dependencies..."
	cd $(BACKEND_DIR) && uv sync --all-groups
	@echo ""
	@echo "==> Installing frontend dependencies..."
	cd $(FRONTEND_DIR) && npm install
	@echo ""
	@echo "============================================"
	@echo "  Setup complete! Next steps:"
	@echo ""
	@echo "  1. Edit .env.dev             (add ANTHROPIC_API_KEY)"
	@echo "  2. Edit $(BACKEND_DIR)/.env  (add ZITADEL_PAT, generate keys)"
	@echo "  3. Edit $(FRONTEND_DIR)/.env.local (add VITE_OIDC_CLIENT_ID)"
	@echo "  4. make dev-up               (start Docker services)"
	@echo "  5. make migrate              (run database migrations)"
	@echo "  6. make backend              (start API server)"
	@echo "  7. make frontend             (start Vite dev server)"
	@echo ""
	@echo "  Full guide: docs/runbooks/local-dev.md"
	@echo "============================================"

# ── Docker Services ──────────────────────────────────────────────────────────

dev-up: ## Start Docker services (PostgreSQL, Redis, MongoDB, Meilisearch, LiteLLM)
	$(COMPOSE) up -d
	@echo "Waiting for services to be healthy..."
	@$(COMPOSE) ps --format "table {{.Name}}\t{{.Status}}"

dev-down: ## Stop Docker services (keep data)
	$(COMPOSE) down

dev-reset: ## Stop services AND delete all data volumes (clean start)
	$(COMPOSE) down -v
	@echo "All volumes removed. Run 'make dev-up && make migrate' to start fresh."

dev-status: ## Show status of Docker services
	$(COMPOSE) ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

dev-logs: ## Tail logs from all Docker services
	$(COMPOSE) logs -f

# ── Backend ──────────────────────────────────────────────────────────────────

backend: ## Start FastAPI backend with hot reload (port 8010)
	cd $(BACKEND_DIR) && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8010

migrate: ## Run Alembic database migrations
	cd $(BACKEND_DIR) && uv run alembic upgrade head

# ── Frontend ─────────────────────────────────────────────────────────────────

frontend: ## Start Vite dev server (port 5174)
	cd $(FRONTEND_DIR) && npm run dev

# ── Quality ──────────────────────────────────────────────────────────────────

lint: ## Run linters (ruff + eslint)
	cd $(BACKEND_DIR) && uv run ruff check .
	cd $(FRONTEND_DIR) && npm run lint

check: ## Run type checks (pyright + tsc)
	cd $(BACKEND_DIR) && uv run pyright
	cd $(FRONTEND_DIR) && npx tsc --noEmit
