#!/usr/bin/env bash
# push-health.sh — Push container health to Uptime Kuma
#
# Runs every minute via cron (klai user).
# Two check methods:
#   push_healthcheck — reads Docker-native healthcheck status (docker inspect)
#   push_exec        — tests connectivity via docker exec from a container on the same network
#
# To add a new service:
#   1. Create a push monitor in Uptime Kuma, copy the token
#   2. Add KUMA_TOKEN_<NAME>=<token> to your config.env and redeploy (deploy.sh main)
#   3. Add push_healthcheck or push_exec line below using the variable
#   4. Run: crontab -e  (entry is already present — no change needed)
set -uo pipefail

# Load push tokens from main env (deployed from config.sops.env)
# shellcheck source=/dev/null
[ -f /opt/klai/.env ] && source /opt/klai/.env

KUMA="${UPTIME_KUMA_PUSH_URL:-https://status.${DOMAIN}/api/push}"
LOG=/opt/klai/logs/health.log

mkdir -p /opt/klai/logs

# Push based on Docker-native healthcheck status (requires healthcheck: in compose)
push_healthcheck() {
    local container="$1" token="$2" label="$3"
    local health
    health=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "missing")
    if [ "$health" = "healthy" ]; then
        curl -sf "${KUMA}/${token}?status=up&msg=OK" -o /dev/null
    else
        curl -sf "${KUMA}/${token}?status=down&msg=${health}" -o /dev/null
        echo "$(date -Iseconds) WARN ${label}: ${health}" >> "$LOG"
    fi
}

# Push based on connectivity test via docker exec (for services on isolated networks)
push_exec() {
    local container="$1" cmd="$2" token="$3" label="$4"
    if docker exec "$container" sh -c "$cmd" &>/dev/null; then
        curl -sf "${KUMA}/${token}?status=up&msg=OK" -o /dev/null
    else
        curl -sf "${KUMA}/${token}?status=down&msg=unreachable" -o /dev/null
        echo "$(date -Iseconds) WARN ${label}: exec check failed" >> "$LOG"
    fi
}

# ── Services with Docker healthchecks ────────────────────────────────────────
# ── Product monitors (status.getklai.com Products section) ──────────────
# Chat: LibreChat health endpoint
push_exec klai-core-portal-api-1 \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://librechat-klai:3080/health')\"" \
    "${KUMA_TOKEN_CHAT}" "Chat"

push_healthcheck klai-core-mongodb-1  "${KUMA_TOKEN_MONGODB}"  "Conversations database"
push_healthcheck klai-core-postgres-1 "${KUMA_TOKEN_POSTGRES}" "Account database"
push_healthcheck klai-core-redis-1    "${KUMA_TOKEN_REDIS}"    "AI Request Cache"

# ── Services on isolated networks (tested from a connected container) ─────────
# Meilisearch: via librechat-klai (net-meilisearch)
push_exec klai-core-librechat-klai-1 \
    "wget -qO- http://meilisearch:7700/health 2>/dev/null | grep -q available" \
    "${KUMA_TOKEN_MEILI}" "Search system"

# Ollama: via litellm (klai-inference)
push_exec klai-core-litellm-1 \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://ollama:11434/')\"" \
    "${KUMA_TOKEN_OLLAMA}" "Backup Language Model (LLM)"

# ── Scribe + Research (via portal-api on klai-net) ───────────────────────────
# scribe-api: receives audio, calls whisper-server internally
push_exec klai-core-portal-api-1 \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://scribe-api:8020/health')\"" \
    "${KUMA_TOKEN_SCRIBE}" "Scribe"

# whisper-server: transcription engine behind scribe-api
push_exec klai-core-portal-api-1 \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://whisper-server:8000/health')\"" \
    "${KUMA_TOKEN_WHISPER}" "Transcription engine"

# research-api: Focus notebooks + document Q&A
push_exec klai-core-portal-api-1 \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://research-api:8030/health')\"" \
    "${KUMA_TOKEN_FOCUS}" "Focus"

# ── Additional services on klai-net (via portal-api) ─────────────────────────
# docs-app: Next.js app with no /api/health route — check TCP reachability instead
push_exec klai-core-portal-api-1 \
    "python3 -c \"import socket; s=socket.create_connection(('docs-app',3010),timeout=5); s.close()\"" \
    "${KUMA_TOKEN_DOCS}" "Docs"

# gitea: Knowledge Base content store (internal only)
push_exec klai-core-portal-api-1 \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://gitea:3000/api/healthz')\"" \
    "${KUMA_TOKEN_GITEA}" "Knowledge Base storage"

# Knowledge stack (tokens require manual Uptime Kuma setup + config.sops.env — see README.md)

# qdrant: vector store for Knowledge module
push_exec klai-core-portal-api-1 \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://qdrant:6333/healthz')\"" \
    "${KUMA_TOKEN_QDRANT:-}" "Vector database"

# knowledge-ingest: RAG ingestion pipeline + LiteLLM pre-call hook
push_exec klai-core-portal-api-1 \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://knowledge-ingest:8000/health')\"" \
    "${KUMA_TOKEN_KNOWLEDGE:-}" "Knowledge ingestion"

# infinity-reranker: cross-encoder reranking for RAG retrieval
push_exec klai-core-portal-api-1 \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://infinity-reranker:7997/health')\"" \
    "${KUMA_TOKEN_RERANKER:-}" "Reranker"

# firecrawl-api: web scraper for research-api (internal only)
push_exec klai-core-portal-api-1 \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://firecrawl-api:3002/')\"" \
    "${KUMA_TOKEN_FIRECRAWL:-}" "Firecrawl"

# tei: text embeddings inference (BAAI/bge-m3, dense)
push_exec klai-core-portal-api-1 \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://tei:8080/health')\"" \
    "${KUMA_TOKEN_TEI:-}" "Embeddings"

# ── Meeting bots ──────────────────────────────────────────────────────────────
# vexa-bot-manager: meeting bot lifecycle manager (Docker-native healthcheck)
push_healthcheck klai-core-vexa-bot-manager-1 "${KUMA_TOKEN_VEXA:-}" "Meeting bot manager"
