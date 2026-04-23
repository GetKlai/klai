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

# Resolve container name by compose project + service label.
# Coolify may prefix container names with a hash after redeploy; this finds
# the actual running container regardless of name prefix.
resolve_container() {
    docker ps \
        --filter "label=com.docker.compose.project=klai-core" \
        --filter "label=com.docker.compose.service=$1" \
        --format "{{.Names}}" | head -1
}

# Resolve exec proxy and healthcheck containers once at startup
PORTAL_API=$(resolve_container portal-api)
LIBRECHAT=$(resolve_container librechat-klai)
LITELLM=$(resolve_container litellm)
MONGODB=$(resolve_container mongodb)
POSTGRES=$(resolve_container postgres)
REDIS=$(resolve_container redis)
VEXA=$(resolve_container vexa-bot-manager)

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
    if [ -z "$container" ]; then
        curl -sf "${KUMA}/${token}?status=down&msg=container-not-found" -o /dev/null
        echo "$(date -Iseconds) WARN ${label}: container not found" >> "$LOG"
        return
    fi
    if docker exec "$container" sh -c "$cmd" &>/dev/null; then
        curl -sf "${KUMA}/${token}?status=up&msg=OK" -o /dev/null
    else
        curl -sf "${KUMA}/${token}?status=down&msg=unreachable" -o /dev/null
        echo "$(date -Iseconds) WARN ${label}: exec check failed" >> "$LOG"
    fi
}

# ── Products ──────────────────────────────────────────────────────────────────

# Chat: LibreChat health endpoint
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://librechat-klai:3080/health')\"" \
    "${KUMA_TOKEN_CHAT}" "Chat"

# Scribe: scribe-api receives audio, calls whisper-server internally
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://scribe-api:8020/health')\"" \
    "${KUMA_TOKEN_SCRIBE}" "Scribe"

# Docs: Next.js app — check TCP reachability (no /health route)
push_exec "$PORTAL_API" \
    "python3 -c \"import socket; s=socket.create_connection(('docs-app',3010),timeout=5); s.close()\"" \
    "${KUMA_TOKEN_DOCS}" "Docs"

# Knowledge: knowledge-ingest product-level (RAG ingestion pipeline)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://knowledge-ingest:8000/health')\"" \
    "${KUMA_TOKEN_KNOWLEDGE:-}" "Knowledge"

# ── Infrastructure ────────────────────────────────────────────────────────────

# Portal API: tenant provisioning + auth gateway
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://localhost:8010/health')\"" \
    "${KUMA_TOKEN_PORTAL_API:-}" "Portal API"

# MongoDB: conversation store (Chat)
push_healthcheck "$MONGODB"  "${KUMA_TOKEN_MONGODB}"  "Conversations Database"

# PostgreSQL: accounts, meetings, knowledge (shared)
push_healthcheck "$POSTGRES" "${KUMA_TOKEN_POSTGRES}" "Account Database"

# Redis: LLM request cache + LibreChat session store
push_healthcheck "$REDIS"    "${KUMA_TOKEN_REDIS}"    "AI Request Cache"

# Meilisearch: LibreChat message search index
push_exec "$LIBRECHAT" \
    "wget -qO- http://meilisearch:7700/health 2>/dev/null | grep -q available" \
    "${KUMA_TOKEN_MEILI}" "Message Search"

# Ollama: local fallback LLM (backup for LiteLLM)
push_exec "$LITELLM" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://ollama:11434/')\"" \
    "${KUMA_TOKEN_OLLAMA}" "Backup Language Model"

# Whisper: transcription engine (Scribe + Meetings via portal-api)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://whisper-server:8000/health')\"" \
    "${KUMA_TOKEN_WHISPER}" "Transcription Engine"

# Docling: document-to-markdown conversion (knowledge-ingest only since SPEC-PORTAL-UNIFY-KB-001)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://docling-serve:5001/health')\"" \
    "${KUMA_TOKEN_DOCLING:-}" "Document Processing"

# Gitea: docs content store (Docs product, Knowledge webhook source)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://gitea:3000/api/healthz')\"" \
    "${KUMA_TOKEN_GITEA}" "Docs Storage"

# Qdrant: vector store for Knowledge retrieval
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://qdrant:6333/healthz')\"" \
    "${KUMA_TOKEN_QDRANT:-}" "Vector Database"

# TEI: text embeddings (Knowledge ingestion + Focus retrieval)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://tei:8080/health')\"" \
    "${KUMA_TOKEN_TEI:-}" "Embeddings"

# Infinity Reranker: cross-encoder reranking for Chat RAG retrieval
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://infinity-reranker:7997/health')\"" \
    "${KUMA_TOKEN_RERANKER:-}" "Reranker"

# Firecrawl: web content fetcher (Chat web mode)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://firecrawl-api:3002/')\"" \
    "${KUMA_TOKEN_FIRECRAWL:-}" "Web Content Fetcher"

# SearXNG: privacy-preserving web search (Chat + Focus)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://searxng:8080/')\"" \
    "${KUMA_TOKEN_SEARXNG:-}" "Web Search"

# Mailer: transactional email service
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://klai-mailer:8000/health')\"" \
    "${KUMA_TOKEN_MAILER:-}" "Email Service"

# Vexa bot manager: meeting bot lifecycle (Docker-native healthcheck)
push_healthcheck "$VEXA" "${KUMA_TOKEN_VEXA:-}" "Meeting Bot Manager"

# ── Knowledge layer (service-level) ──────────────────────────────────────────

# Knowledge Ingestion: RAG pipeline service (separate from Products "Knowledge")
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://knowledge-ingest:8000/health')\"" \
    "${KUMA_TOKEN_KNOWLEDGE_INGEST:-}" "Knowledge Ingestion"

# External Source Sync: klai-connector syncs GitHub → knowledge-ingest
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://klai-connector:8200/health')\"" \
    "${KUMA_TOKEN_CONNECTOR:-}" "External Source Sync"

# Knowledge MCP: bridge between Chat (LibreChat) and Knowledge layer
push_exec "$PORTAL_API" \
    "python3 -c \"import socket; s=socket.create_connection(('klai-knowledge-mcp',8080),timeout=5); s.close()\"" \
    "${KUMA_TOKEN_KNOWLEDGE_MCP:-}" "Knowledge MCP"

# FalkorDB: graph database (Knowledge graph store — Graphiti)
push_exec "$PORTAL_API" \
    "python3 -c \"import socket; s=socket.create_connection(('falkordb',6379),timeout=5); s.close()\"" \
    "${KUMA_TOKEN_FALKORDB:-}" "Graph Database"

# Retrieval API: hybrid vector + graph search (Knowledge product)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://retrieval-api:8040/health')\"" \
    "${KUMA_TOKEN_RETRIEVAL_API:-}" "Retrieval API"
