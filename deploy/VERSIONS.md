# Intentional Version Pins

This file documents every version constraint that deviates from "latest" and the reason why.
**Before upgrading any of these, read the rationale and test the upgrade path.**

Automated dependency updates are handled by Dependabot (`.github/dependabot.yml`).
Dependabot ignores the constraints listed here via its `ignore` rules.

---

## Docker images

### `pgvector/pgvector:pg17` (main postgres)

**Current:** pg17 (PostgreSQL 17.x with pgvector 0.8.2)
**Latest available:** pg18

**Why pinned:** PostgreSQL major version upgrades require running `pg_upgrade` — you cannot simply change the image tag. pg17 is supported until November 2029.

**Upgrade path:**
1. Spin up a pg18 instance alongside pg17
2. Run `pg_upgrade --old-datadir ... --new-datadir ...`
3. Verify all data, run migrations, then cut over
4. Update tag to `pgvector/pgvector:pg18`

---

### `postgres:16-alpine` (firecrawl-postgres)

**Current:** PostgreSQL 16
**Latest available:** PostgreSQL 18

**Why pinned:** This is the Firecrawl-internal database, separate from the main klai postgres. Firecrawl was configured against pg16. pg16 is supported until November 2028.

**Upgrade path:** Check Firecrawl release notes for pg17/pg18 support, then apply the same pg_upgrade procedure as above (smaller dataset, lower risk).

---

### `rabbitmq:3-alpine` (firecrawl-rabbitmq)

**Current:** RabbitMQ 3.x (latest 3.x patch)
**Latest available:** RabbitMQ 4.2.5

**Why pinned:** RabbitMQ 4.0 made AMQP 1.0 the default protocol (breaking change from 3.x's AMQP 0-9-1 default). Firecrawl has not published confirmed support for RabbitMQ 4.x.

**Upgrade path:** Check [Firecrawl releases](https://github.com/firecrawl/firecrawl/releases) for RabbitMQ 4 compatibility statement, then test with `rabbitmq:4-alpine` in a staging environment before promoting.

---

## Python packages

### `procrastinate>=2.15,<3` (knowledge-ingest)

**Current:** procrastinate 2.x (latest: 2.15.1)
**Latest available:** 3.7.2

**Why pinned:** knowledge-ingest uses procrastinate 2.x-specific API:
- `procrastinate.PsycopgConnector` (connector class name changed in 3.x)
- `app.open_async()` / `app.run_worker_async()` (worker API changed in 3.x)

Dependabot is configured to ignore procrastinate `>=3` for knowledge-ingest.

**Upgrade path:**
1. Read [procrastinate 3.x changelog](https://procrastinate.readthedocs.io/en/stable/changelog.html)
2. Update connector and worker calls in `knowledge_ingest/app.py` and `knowledge_ingest/enrichment_tasks.py`
3. Run procrastinate DB migrations: `procrastinate migrate`
4. Update pyproject.toml constraint to `>=3.0,<4`

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
