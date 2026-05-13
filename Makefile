# Klai Local Development
# Run `make help` to see available targets.

COMPOSE := docker compose -f docker-compose.dev.yml --env-file .env.dev
BACKEND_DIR := klai-portal/backend
FRONTEND_DIR := klai-portal/frontend

.PHONY: help setup dev-up dev-down dev-reset dev-status dev-logs seed backend frontend migrate lint check

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
	@test -f $(BACKEND_DIR)/.env || { \
		cp $(BACKEND_DIR)/.env.example $(BACKEND_DIR)/.env && \
		echo "  Generating encryption keys..." && \
		SECRETS_KEY=$$(python3 -c "import secrets; print(secrets.token_hex(32))") && \
		ENCRYPT_KEY=$$(python3 -c "import secrets; print(secrets.token_hex(32))") && \
		COOKIE_KEY=$$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") && \
		sed -i.bak "s|^PORTAL_SECRETS_KEY=.*|PORTAL_SECRETS_KEY=$$SECRETS_KEY|" $(BACKEND_DIR)/.env && \
		sed -i.bak "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=$$ENCRYPT_KEY|" $(BACKEND_DIR)/.env && \
		sed -i.bak "s|^SSO_COOKIE_KEY=.*|SSO_COOKIE_KEY=$$COOKIE_KEY|" $(BACKEND_DIR)/.env && \
		rm -f $(BACKEND_DIR)/.env.bak && \
		echo "  Keys generated and written to $(BACKEND_DIR)/.env"; \
	}
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
	@echo "  1. make dev-up               (start Docker services)"
	@echo "  2. make migrate              (run database migrations)"
	@echo "  3. make backend              (start API — auto-creates dev user)"
	@echo "  4. make frontend             (start Vite dev server)"
	@echo ""
	@echo "  That's it! No env editing needed for standalone mode."
	@echo "  For AI features: add ANTHROPIC_API_KEY to .env.dev"
	@echo "  For production Zitadel: see docs/runbooks/local-dev.md"
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

# ── Data ─────────────────────────────────────────────────────────────────────

seed: ## Seed database with demo data (dev org, users)
	docker exec -i klai-postgres-1 psql -U klai -d klai < dev/seed.sql
	@echo "Database seeded with demo data."

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
