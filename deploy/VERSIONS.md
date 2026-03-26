# Intentional Version Pins

This file documents every version constraint that deviates from "latest" and the reason why.
**Before upgrading any of these, read the rationale and test the upgrade path.**

Automated dependency updates are handled by Dependabot (`.github/dependabot.yml`).
Dependabot ignores the constraints listed here via its `ignore` rules.

---

## Docker images

### `pgvector/pgvector:pg18` (main postgres)

**Current:** pg18 (PostgreSQL 18.x with pgvector 0.8.2)
**Latest available:** pg18 ✅ up to date

**Why pinned:** PostgreSQL major version upgrades require dump/restore. pg18 is the current major version (stable since Sept 2025). Upgraded from pg17 on 2026-03-26 via dump/restore.

**Next upgrade path (to pg19 when released):**
1. `pg_dumpall -U klai` → save to /opt/klai/backups/
2. Stop all services, change image, delete postgres-data volume
3. Start fresh pg18→pg19, restore dump
4. Start all services

---

### `postgres:18-alpine` (firecrawl-postgres)

**Current:** PostgreSQL 18
**Latest available:** PostgreSQL 18 ✅ up to date

**Note:** Firecrawl-internal queue database (NUQ schema). Data is transient (queue state). Upgraded from pg16 on 2026-03-26 — data volume deleted and recreated clean.

---

### `rabbitmq:3-alpine` (firecrawl-rabbitmq)

**Current:** RabbitMQ 3.x (latest 3.x patch)
**Latest available:** RabbitMQ 4.2.5

**Why pinned:** RabbitMQ 4.0 made AMQP 1.0 the default protocol (breaking change from 3.x's AMQP 0-9-1 default). Firecrawl has not published confirmed support for RabbitMQ 4.x.

**Upgrade path:** Check [Firecrawl releases](https://github.com/firecrawl/firecrawl/releases) for RabbitMQ 4 compatibility statement, then test with `rabbitmq:4-alpine` in a staging environment before promoting.

---

## Python packages

### `procrastinate>=3.0,<4` (knowledge-ingest)

**Current:** 3.7.2 ✅ up to date
**Upgraded from:** 2.15.1 on 2026-03-26

API unchanged between 2.x and 3.x (PsycopgConnector, open_async(), run_worker_async() all the same). DB schema was already 3.x-compatible (queueing_lock + full status enum were added in procrastinate 2.15). No DB migration was needed.

---

### `graphiti-core>=0.28,<0.30` (retrieval-api)

**Current:** 0.28.2
**Latest available:** 0.28.2 stable (0.30.0 is pre-release/rc)

**Why pinned:** Upper bound `<0.30` prevents accidental installation of the 0.30.x release candidate which introduced breaking API changes.

**Upgrade path:** When 0.30.x reaches stable on PyPI, test retrieval-api against it and remove the `<0.30` upper bound.

---

## What is NOT pinned (intentional)

All other Docker images use `:latest`. On `core-01`, run `docker compose pull` periodically to actually pull new versions — the image only updates on the server when explicitly pulled.

Recommended maintenance cadence: monthly `docker compose pull && docker compose up -d` on core-01 during a low-traffic window.
