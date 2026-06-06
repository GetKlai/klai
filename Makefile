# Klai Local Development
# Run `make help` to see available targets.

COMPOSE := docker compose -f docker-compose.dev.yml --env-file .env.dev
BACKEND_DIR := klai-portal/backend
FRONTEND_DIR := klai-portal/frontend
FRONTEND_PORT ?= $(if $(CONDUCTOR_PORT),$(CONDUCTOR_PORT),5174)
BACKEND_PORT ?= $(if $(CONDUCTOR_PORT),$(shell expr $(CONDUCTOR_PORT) + 1),8010)
FRONTEND_URL ?= http://localhost:$(FRONTEND_PORT)

.PHONY: help setup local-dev-status e2e-prod-status dev-up dev-down dev-reset dev-status dev-logs seed postdeploy backend frontend migrate lint check

# ── Help ─────────────────────────────────────────────────────────────────────

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── Setup ────────────────────────────────────────────────────────────────────

setup: ## First-time setup: copy env files, generate keys, install dependencies
	@echo "==> Copying environment files..."
	@test -f .env.dev.example || { echo "ERROR: .env.dev.example not found. Are you in the repo root?"; exit 1; }
	@test -f $(BACKEND_DIR)/.env.example || { echo "ERROR: $(BACKEND_DIR)/.env.example not found."; exit 1; }
	@test -f $(FRONTEND_DIR)/.env.local.example || { echo "ERROR: $(FRONTEND_DIR)/.env.local.example not found."; exit 1; }
	@test -f .env.dev || cp .env.dev.example .env.dev
	@test -f $(FRONTEND_DIR)/.env.development.local || cp $(FRONTEND_DIR)/.env.local.example $(FRONTEND_DIR)/.env.development.local
	@test -f $(BACKEND_DIR)/.env || { \
		cp $(BACKEND_DIR)/.env.example $(BACKEND_DIR)/.env && \
		echo "  Generating encryption keys (stdlib only — no cryptography import)..." && \
		SECRETS_KEY=$$(python3 -c "import secrets; print(secrets.token_hex(32))") && \
		ENCRYPT_KEY=$$(python3 -c "import secrets; print(secrets.token_hex(32))") && \
		COOKIE_KEY=$$(python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())") && \
		sed -i.bak "s|^PORTAL_SECRETS_KEY=.*|PORTAL_SECRETS_KEY=$$SECRETS_KEY|" $(BACKEND_DIR)/.env && \
		sed -i.bak "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=$$ENCRYPT_KEY|" $(BACKEND_DIR)/.env && \
		sed -i.bak "s|^SSO_COOKIE_KEY=.*|SSO_COOKIE_KEY=$$COOKIE_KEY|" $(BACKEND_DIR)/.env && \
		rm -f $(BACKEND_DIR)/.env.bak && \
		echo "  Keys generated and written to $(BACKEND_DIR)/.env"; \
	}
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
	@echo "  1. make dev-up               (start Docker services)"
	@echo "  2. make migrate              (run database migrations)"
	@echo "  3. make backend              (start API — auto-creates dev user)"
	@echo "  4. make frontend             (start Vite dev server)"
	@echo ""
	@echo "  That's it! No env editing needed for standalone mode."
	@echo "  For AI features: add ANTHROPIC_API_KEY to .env.dev"
	@echo "  For production Zitadel: see docs/runbooks/local-dev.md"
	@echo "============================================"

local-dev-status: ## Explain and validate the local standalone dev/browser-test setup
	@FRONTEND_PORT=$(FRONTEND_PORT) BACKEND_PORT=$(BACKEND_PORT) VITE_API_PROXY_TARGET=$${VITE_API_PROXY_TARGET:-http://localhost:$(BACKEND_PORT)} scripts/local-dev-status.sh --mode local

e2e-prod-status: ## Explain and validate the production E2E setup
	@scripts/local-dev-status.sh --mode prod-e2e

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

# ── Data ─────────────────────────────────────────────────────────────────────

# Auto-discover the compose-managed postgres container (works regardless of
# project name; canonical "klai-postgres-1", workspace "<dir>-postgres-1").
PG_CONTAINER := $$(docker ps --format '{{.Names}}' | grep -E '^[a-z0-9_-]+-postgres-1$$' | head -1)

seed: ## Seed database with demo data (dev org, users)
	@CTR="$(PG_CONTAINER)"; \
	if [ -z "$$CTR" ]; then echo "No postgres container found — run 'make dev-up' first."; exit 1; fi; \
	docker exec -i "$$CTR" psql -U klai -d klai < dev/seed.sql; \
	echo "Database seeded with demo data (container=$$CTR)."

postdeploy: ## Apply post-deploy SQL (RLS policies + helper functions) as klai superuser
	@CTR="$(PG_CONTAINER)"; \
	if [ -z "$$CTR" ]; then echo "No postgres container found — run 'make dev-up' first."; exit 1; fi; \
	cd $(BACKEND_DIR) && bash scripts/apply_post_deploy_sql.sh --local --container "$$CTR"

# ── Backend ──────────────────────────────────────────────────────────────────

backend: ## Start FastAPI backend with hot reload (default port 8010, or CONDUCTOR_PORT+1)
	cd $(BACKEND_DIR) && FRONTEND_URL=$(FRONTEND_URL) CORS_ORIGINS=$(FRONTEND_URL) uv run uvicorn app.main:app --reload --host 0.0.0.0 --port $(BACKEND_PORT)

migrate: ## Run Alembic database migrations
	cd $(BACKEND_DIR) && uv run alembic upgrade head

# ── Frontend ─────────────────────────────────────────────────────────────────

frontend: ## Start Vite dev server (default port 5174, or CONDUCTOR_PORT)
	cd $(FRONTEND_DIR) && VITE_API_PROXY_TARGET=$${VITE_API_PROXY_TARGET:-http://localhost:$(BACKEND_PORT)} VITE_DEV_SERVER_PORT=$(FRONTEND_PORT) npm run dev -- --host 127.0.0.1 --port $(FRONTEND_PORT) --strictPort

# ── Quality ──────────────────────────────────────────────────────────────────

lint: ## Run linters (ruff + eslint)
	cd $(BACKEND_DIR) && uv run ruff check .
	cd $(FRONTEND_DIR) && npm run lint

check: ## Run type checks (pyright + tsc)
	cd $(BACKEND_DIR) && uv run pyright
	cd $(FRONTEND_DIR) && npx tsc --noEmit
