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
set -eo pipefail

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
# librechat-getklai is the Klai-tenant LibreChat instance (renamed from
# librechat-klai after SPEC-PROVISIONING introduced per-tenant slug naming).
LIBRECHAT=$(resolve_container librechat-getklai)
LITELLM=$(resolve_container litellm)
MONGODB=$(resolve_container mongodb)
POSTGRES=$(resolve_container postgres)
REDIS=$(resolve_container redis)
# api-gateway is the public-facing entrypoint to the Vexa V3 meeting stack
# (replaces the legacy vexa-bot-manager monolith — SPEC-VEXA-001/003).
# api-gateway healthcheck is self-only (no dependency probe), so meeting-api,
# runtime-api and admin-api are monitored separately below.
MEETING=$(resolve_container api-gateway)
MEETING_API=$(resolve_container meeting-api)
MEETING_RUNTIME=$(resolve_container runtime-api)
MEETING_ADMIN=$(resolve_container admin-api)
GARAGE=$(resolve_container garage)
CAL_COM=$(resolve_container cal-com)
VAULTWARDEN=$(resolve_container vaultwarden)
VICTORIALOGS=$(resolve_container victorialogs)
VICTORIAMETRICS=$(resolve_container victoriametrics)
ALLOY=$(resolve_container alloy)

# Push based on Docker-native healthcheck status (requires healthcheck: in compose)
push_healthcheck() {
    local container="$1" token="$2" label="$3"
    [ -z "$token" ] && return      # skip if no token configured
    [ -z "$container" ] && {       # container not found — report down
        curl -sf "${KUMA}/${token}?status=down&msg=container-not-found" -o /dev/null
        echo "$(date -Iseconds) WARN ${label}: container not found" >> "$LOG"
        return
    }
    local health
    health=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "missing")
    health=$(echo "$health" | tr -d '\n\r')  # strip newlines — prevent curl URL parse failure
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
    [ -z "$token" ] && return  # skip if no token configured
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

# Chat: LibreChat health endpoint (Klai-tenant instance)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://librechat-getklai:3080/health')\"" \
    "${KUMA_TOKEN_CHAT:-}" "Chat"

# Scribe: receives audio (upload via scribe-api OR live Vexa-bot via api-gateway),
# transcribes via gpu-01 Vexa workers, summarizes via LiteLLM. Composite check —
# product is only healthy when BOTH the upload-path entry (scribe-api) AND the
# meeting-bot entry (api-gateway) are responding. Granular per-component
# detail lives in the "Meeting recordings & transcripts" group.
scribe_composite_status() {
    # Probe 1: scribe-api /health (upload path)
    if ! docker exec "$PORTAL_API" \
        python3 -c "import urllib.request; urllib.request.urlopen('http://scribe-api:8020/health', timeout=5)" 2>/dev/null; then
        echo "down&msg=scribe-api-unreachable"
        return
    fi
    # Probe 2: Vexa api-gateway healthcheck (live-bot path)
    if [ -z "$MEETING" ]; then
        echo "down&msg=api-gateway-missing"
        return
    fi
    local health
    health=$(docker inspect --format='{{.State.Health.Status}}' "$MEETING" 2>/dev/null | tr -d '\n\r' || echo missing)
    if [ "$health" != "healthy" ]; then
        echo "down&msg=meeting-bot-${health}"
        return
    fi
    echo "up&msg=OK"
}
if [ -n "${KUMA_TOKEN_SCRIBE:-}" ]; then
    SCRIBE_STATUS=$(scribe_composite_status)
    curl -sf "${KUMA}/${KUMA_TOKEN_SCRIBE}?status=${SCRIBE_STATUS}" -o /dev/null
    [[ "$SCRIBE_STATUS" == down* ]] && echo "$(date -Iseconds) WARN Scribe: ${SCRIBE_STATUS#down&msg=}" >> "$LOG"
fi

# Docs: Next.js app — check TCP reachability (no /health route)
push_exec "$PORTAL_API" \
    "python3 -c \"import socket; s=socket.create_connection(('docs-app',3010),timeout=5); s.close()\"" \
    "${KUMA_TOKEN_DOCS:-}" "Docs"

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
push_healthcheck "$MONGODB"  "${KUMA_TOKEN_MONGODB:-}"  "Conversations Database"

# PostgreSQL: accounts, meetings, knowledge (shared)
push_healthcheck "$POSTGRES" "${KUMA_TOKEN_POSTGRES:-}" "Account Database"

# Redis: LLM request cache + LibreChat session store
push_healthcheck "$REDIS"    "${KUMA_TOKEN_REDIS:-}"    "AI Request Cache"

# Meilisearch: LibreChat message search index (probed from portal-api network)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request, json; d=json.loads(urllib.request.urlopen('http://meilisearch:7700/health').read()); assert d['status']=='available'\"" \
    "${KUMA_TOKEN_MEILI:-}" "Message Search"

# Ollama: local fallback LLM (backup for LiteLLM)
push_exec "$LITELLM" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://ollama:11434/')\"" \
    "${KUMA_TOKEN_OLLAMA:-}" "Backup Language Model"

# Whisper: transcription engine (now on gpu-01 via SSH tunnel at 172.18.0.1:8000)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://172.18.0.1:8000/health')\"" \
    "${KUMA_TOKEN_WHISPER:-}" "Transcription Engine"

# Docling: document-to-markdown conversion (knowledge-ingest only since SPEC-PORTAL-UNIFY-KB-001)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://docling-serve:5001/health')\"" \
    "${KUMA_TOKEN_DOCLING:-}" "Document Processing"

# Gitea: docs content store (Docs product, Knowledge webhook source)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://gitea:3000/api/healthz')\"" \
    "${KUMA_TOKEN_GITEA:-}" "Docs Storage"

# Qdrant: vector store for Knowledge retrieval
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://qdrant:6333/healthz')\"" \
    "${KUMA_TOKEN_QDRANT:-}" "Vector Database"

# TEI: dense text embeddings (TEI on gpu-01 via SSH tunnel at 172.18.0.1:7997)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://172.18.0.1:7997/health')\"" \
    "${KUMA_TOKEN_TEI:-}" "Embeddings"

# Infinity Reranker: cross-encoder reranking (Infinity on gpu-01 via SSH tunnel at 172.18.0.1:7998)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://172.18.0.1:7998/health')\"" \
    "${KUMA_TOKEN_RERANKER:-}" "Reranker"

# BGE-M3 sparse: sparse embeddings (gpu-01 via SSH tunnel at 172.18.0.1:8001)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://172.18.0.1:8001/health')\"" \
    "${KUMA_TOKEN_BGE_SPARSE:-}" "BGE-M3 Sparse"

# Firecrawl: web content fetcher (Chat web mode)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://firecrawl-api:3002/')\"" \
    "${KUMA_TOKEN_FIRECRAWL:-}" "Web Content Fetcher"

# SearXNG: privacy-preserving web search
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://searxng:8080/')\"" \
    "${KUMA_TOKEN_SEARXNG:-}" "Web Search"

# Mailer: transactional email service
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://klai-mailer:8000/health')\"" \
    "${KUMA_TOKEN_MAILER:-}" "Email Service"

# Meeting service: Vexa V3 stack public entrypoint (api-gateway) — Docker healthcheck
# Replaces legacy vexa-bot-manager monolith (SPEC-VEXA-001/003).
push_healthcheck "$MEETING"         "${KUMA_TOKEN_VEXA:-}"          "Meeting service"

# Vexa V3 internal stack — api-gateway healthcheck is self-only, so probe each
# internal component independently to surface partial-failure modes.
push_healthcheck "$MEETING_API"     "${KUMA_TOKEN_VEXA_MEETING:-}"  "Meeting API"
push_healthcheck "$MEETING_RUNTIME" "${KUMA_TOKEN_VEXA_RUNTIME:-}"  "Meeting Runtime"
push_healthcheck "$MEETING_ADMIN"   "${KUMA_TOKEN_VEXA_ADMIN:-}"    "Meeting Admin"

# Garage: S3-compatible object storage (kb-images, scribe-uploads, librechat
# avatars). User-facing impact if down.
push_healthcheck "$GARAGE"          "${KUMA_TOKEN_GARAGE:-}"        "Object Storage"

# Cal.com: meeting bookings (user-facing booking links).
push_healthcheck "$CAL_COM"         "${KUMA_TOKEN_CALCOM:-}"        "Bookings"

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

# Crawl4AI: shared web crawler container (REST API at crawl4ai:11235)
push_exec "$PORTAL_API" \
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://crawl4ai:11235/health')\"" \
    "${KUMA_TOKEN_CRAWL4AI:-}" "Web Crawler"

# ── Internal infrastructure (hidden from public status page) ─────────────────

# VictoriaLogs: log retention (Alloy → here). Down = no debugging visibility.
push_healthcheck "$VICTORIALOGS"    "${KUMA_TOKEN_VICTORIALOGS:-}"     "Log retention (VictoriaLogs)"

# VictoriaMetrics: metrics retention. Down = Grafana data + alerting blind.
push_healthcheck "$VICTORIAMETRICS" "${KUMA_TOKEN_VICTORIAMETRICS:-}"  "Metrics retention (VictoriaMetrics)"

# Alloy: log shipper (Docker socket → VictoriaLogs). The container has no
# probe tools (wget/curl absent) and the http endpoint binds to 127.0.0.1
# inside the container — fall back to bash built-in TCP test against the
# server-http port. Reachable port = Alloy process listening = up.
push_exec "$ALLOY" \
    "bash -c 'exec 3<>/dev/tcp/localhost/12345 && exec 3<&-'" \
    "${KUMA_TOKEN_ALLOY:-}" "Log shipper core-01 (Alloy)"

# Vaultwarden: team password manager. Internal but critical for team operations.
push_healthcheck "$VAULTWARDEN"     "${KUMA_TOKEN_VAULTWARDEN:-}"      "Vaultwarden"

# ── GPU Services (gpu-01 via SSH tunnel) ─────────────────────────────────────

# Combined GPU health: tunnel service + all 4 inference endpoints (TEI,
# Infinity, sparse, transcription LB). Per-worker transcription monitoring
# is in gpu-health.sh (uses ssh to gpu-01 for individual worker probe).
[ -x /opt/klai/scripts/gpu-health.sh ] && bash /opt/klai/scripts/gpu-health.sh
