# Explicit Version Pins

This file documents why an image is held where it is and what to check before a bump. **It never states a version.** The compose files (`deploy/docker-compose.yml`, `deploy/docker-compose.override.yml`, `deploy/docker-compose.gpu.yml`, `docker-compose.dev.yml`) are the only place an image tag or digest lives, because Renovate bumps them and never edits this file; a version written here is wrong at the next bump. To see what is pinned, read the compose file or run `rg -n 'image:' deploy/docker-compose.yml`.

Every external image in `docker-compose.yml` and `docker-compose.dev.yml` is pinned to an explicit version tag (`deploy/check-image-tags.sh`). No `:latest` on external services.

`deploy/check-versions-doc.py` enforces the split in CI and pre-commit: every service named in a table below must exist in compose, and no backticked image reference may carry a tag or digest. Upgrades follow `docs/runbooks/version-management.md`.

**Exception — internal CI-deployed services:** images under `ghcr.io/getklai/*` (portal-api, retrieval-api, knowledge-ingest, klai-connector, klai-mailer, klai-slack, klai-docs, klai-knowledge-mcp, scribe-api, caddy-hetzner) use `:latest` because GitHub Actions rebuild and re-push on every commit to `main` in their respective repos, AND — this is the part that makes the rolling tag safe — the pull and the recreate happen in one `compose-up.sh <service>` call, so a newer `:latest` never sits on the host waiting to be picked up by somebody else's deploy.

Each CI workflow also tags the build with `:${github.sha}` so rollbacks are possible via explicit SHA pin. These are NOT production `:latest` anti-patterns — they are continuous-deployment rolling tags owned by our own CI pipelines. The self-host compose file expects every referenced `ghcr.io/getklai/*` image to be public and anonymously pullable; `deploy/check-image-pullable.sh` enforces that contract. (research-api removed in SPEC-PORTAL-UNIFY-KB-001.)

`whisper-server` is the exception to the exception, and not for the reason this file claimed until 2026-08-17. It is not deployed anywhere — not automatically, and not by hand. `docs/architecture/scribe-transcription.md` calls it what it is: a public reference image for self-hosters who need a Whisper-compatible speech-to-text backend. Klai production has not run it since SPEC-VEXA-003 replaced it with the Vexa transcription workers, which serve the same host port and the same OpenAI-compatible path, so `WHISPER_SERVER_URL` never pointed at a whisper-server container to begin with. A green build here means the reference image is publishable — nothing more, and nothing was waiting on it.

**Exception — local builds:** the `retrieval-api` service in `deploy/docker-compose.override.yml` is built on-host from source and not pullable from a registry. Its "version" is tracked by the git SHA recorded in the compose comments.

`firecrawl-api` used to be built on-host under a name nothing could pull, so the running service could not have been rebuilt from this repository if the host had lost the image. Since 2026-09-10 it is pinned to upstream's own published release, so Renovate tracks it like every other image here. Bumping it means running the comparison recorded in the docker-compose.yml comment first: identical harness and env contract, and LibreChat's `POST /v2/scrape` answering with the same shape.

**Exception — Vexa stack:** `vexaai/v012-admin-api`, `vexaai/v012-meeting-api`, `vexaai/v012-runtime` and the bot image `vexaai/vexa-bot` share one upstream tag (the check in `deploy/check-version-pin-agreement.py` keeps the runtime and the bot together) — pulled directly from Docker Hub (since v0.10.4 upstream publishes pre-built images). The 0.10 line (`admin-api`, `api-gateway`, `meeting-api`, `runtime-api`, and its Redis) was removed in PR #914 after 0.12 was proven end to end; SPEC-VEXA-004 has the migration. Note the tag shape: the 0.10 line published `0.10.6.3.14`, the 0.12 line publishes ONLY the v-prefixed form — a plain `0.12.22` was not on Docker Hub. `deploy/check-image-tags.sh` accepts either. The bot image is an env value rather than a compose service, so `compose up` never fetches it and docker-socket-proxy cannot either; the compose sync pulls it (`deploy/scripts/vexa-bot-prepull.sh`, before any file on core-01 is replaced) and `scripts/check-vexa12-deploy-preconditions.sh` checks the result. That pull is what lets Renovate automerge Vexa bumps. Upgrade cadence: track upstream stable tags; bump for material fixes (chunk-leak, OOM, security, browser/admission fixes). See `https://github.com/Vexa-ai/vexa/releases` for changelog.

GPU production image pins are intentionally not listed in this public repo because the live GPU compose, operator runbooks, host paths, and tunnel details belong in `GetKlai/klai-infra`.

---

## Core stack — `deploy/docker-compose.yml`

### Database layer

| Service | Rationale |
|---|---|
| `postgres` | pgvector on the current Postgres major. PostgreSQL major version upgrades require dump/restore. Upgrade path: `pg_dumpall` → stop services → change image → delete volume → restore dump. A pgvector minor on the same Postgres major is data-compatible. |
| `firecrawl-postgres` | Firecrawl-internal queue DB (NUQ schema). Pinned to match main postgres major. Data is transient (queue state), so cross-major migration is just a volume delete. |
| `listmonk-db` | Dedicated database for listmonk campaigns, subscribers, templates, and admin users. Moved from 17 to 18 on 2026-10-22 by dump and restore into a new volume mounted at `/var/lib/postgresql` (the 18 image keeps PGDATA in `/var/lib/postgresql/18/docker`), rehearsed against listmonk v6.2.0. Upstream's compose file still ships 17; listmonk ran its install, upgrade and public pages on 18 without changes. |
| `mongodb` | Every LibreChat instance depends on it. Community has no binary downgrade, so a bump that raised the feature compatibility version (FCV) can only be undone by mongodump/mongorestore into a new volume (the volume name `mongodb-data-80` is a leftover of such a restore, not the version). Renovate proposes one minor at a time and the compose sync raises the FCV after each 30-minute rollback window (`ds_settle`), so the next one starts. Skipping a minor is not supported. |
| `redis` | Redis 8 ships Vector Sets and hash-field TTL. Security releases within the major are bumped as soon as they land. |
| `vexa12-redis` | Aligned with the main redis major. Isolated network; bot state + pub/sub + transcription streams. |
| `qdrant` | Vector store for Klai Knowledge. Binary-incompatible on major bumps. Before a bump, boot the new version against a COPY of production storage and check that the collection recovers (status green, point count equal, scroll with payload answers, no panics), as was done for 1.19.1. Check the release notes for behaviour changes in query limits and vector validation against our call sites; the bge-m3 vectors are always 1024-dim. |
| `falkordb` | Knowledge graph (Graphiti backend). Persistence is the RDB snapshot only; the data mount matters: a cosmetic mount caused total graph loss on 2026-04-19 when the container was recreated. Read the comments above the service in compose before changing persistence or the query timeout. |

### Auth + monitoring

| Service | Rationale |
|---|---|
| `zitadel` | OIDC IdP. Security releases (account takeover, MFA bypass, Actions sandbox escape in the 4.16.x batch) are bumped promptly. [HIGH] Minor upgrades sometimes invalidate portal-api PAT — see `.claude/rules/klai/platform/zitadel.md`. Rotate PAT after each bump. |
| `victoriametrics` | Metrics TSDB. Security fixes (e.g. the vmrestore path-traversal fix, CVE-2026-61625) are bumped promptly. |
| `victorialogs` | Log aggregation (replaces Loki). LogsQL syntax differs from LogQL. **Held on the 1.51 line: v1.52.0 is scratch-based — no `/bin/sh`/`wget` — which breaks our authenticated CMD-SHELL healthcheck (container flips to unhealthy). Rework the healthcheck (sidecar probe or external-only monitoring) before bumping to 1.52 or later.** |
| `cadvisor` | Container metrics. The registry moved from `gcr.io` to `ghcr.io`; verify dashboards that depend on container start/creation timestamps after a bump. |
| `alloy` | Log and metric collection. Config format stable on minor bumps. |
| `grafana` | Dashboard UI. v12 → v13 had breaking dashboard JSON changes — verify dashboards after any major bump. |
| `glitchtip-web`, `glitchtip-worker`, `glitchtip-migrate` | Error tracking. All three share the same image (different commands). |

### Inference + AI

| Service | Rationale |
|---|---|
| `litellm` | Pinned explicitly (moved from rolling `:main-stable` on 2026-04-19). Re-assess monthly. Upstream discontinued the `-stable` tag suffix family: plain tags on non-prerelease GitHub releases are the stable line now (verified 2026-08-13). The same version is pinned in `.github/workflows/litellm-tests.yml`; `deploy/check-version-pin-agreement.py` keeps them together. |
| `ollama` | CPU fallback for LLM inference. |
| `librechat-getklai` | LibreChat UI for all tenants. The compose service and the provisioning-managed tenant containers run the Klai-owned image built from `deploy/librechat/patches-source/`, pinned by digest (`deploy/check-klai-librechat-digest.sh`) because a tag was once overwritten with different content. On an upstream bump: `@librechat/agents` and `@librechat/data-schemas` switched to a rolldown bundle between v0.8.6 and v0.8.7, which inlined `dist/models/*` into `dist/index.cjs` and crashlooped the first rollout; the entrypoint's Meili patch tries both shapes and throws if neither is present, and `deploy/librechat/check-patch-drift.sh` fails the deploy-compose validate step if any runtime patch target is missing from the image. Re-verify both on every upgrade. |

### Document + search

| Service | Rationale |
|---|---|
| `meilisearch` | Search index for LibreChat conversations. **Data migration required on minor bumps**: an older version refused to boot directly on a newer one, and the last minors were migrated via dump/import with a baseline archived under `backups/` in the deploy directory. Keep `MEILI_DB_PATH=/meili_data`; otherwise it starts on `./data.ms` and ignores the mounted volume. |
| `docling-serve` | Document parsing (PDF, DOCX → structured). |
| `searxng` | Meta-search aggregator for LibreChat web mode. Date-based versioning. |
| `gitea` | Self-hosted git for klai-docs. Upstream renumbered from 1.27 to 28 in 2026-09; Gitea is git-hosting only here, so its Actions and CSP changes do not apply. v28 added an egress filter for outbound git and webhook calls that denied the knowledge-ingest webhooks until compose set `GITEA__webhook__ALLOWED_HOST_LIST: private` (see the comment there). Schema migrations run on start and are one-way: roll back by restoring the `gitea` database dump and the volume. |
| `crawl4ai` | Klai-built image from upstream source (deploy/crawl4ai/Dockerfile, built on core-01, never pushed to a registry); the compose tag carries the upstream version and the build stamp. 0.9.x enforces bearer auth on /crawl; knowledge-ingest and connector both carry the shared key. Hooks are ENABLED in compose: on 0.9.x a hook is a declarative action from a fixed server-authored set, not the exec'd Python that CVE-2026-57572 closed, and leaving the flag at its 0.8.x value silently dropped cookie injection from every authenticated crawl. EXACTLY ONE local patch remains: a request carrying hooks gets its own browser, because upstream attaches hooks to a pooled crawler and never detaches them. `body_visibility_timeout` is upstream config, set per request in knowledge_ingest/crawl4ai_config.py. All three contracts are proven by deploy/tests/crawl4ai-cookie-contract.sh. |
| `presidio-analyzer`, `presidio-anonymizer` | Both digest-pinned, so Renovate cannot follow them; `version-drift.yml` compares against the newest upstream tag. The analyzer is Klai's own image (`ghcr.io/getklai/presidio-analyzer`, upstream version plus a `-klai.N` suffix), rebuilt by presidio-analyzer-image-build.yml on merge and pinned in compose in a follow-up. The anonymizer is upstream's. `deploy/check-klai-presidio-digest.sh` enforces the digest form. |

### Ops

| Service | Rationale |
|---|---|
| `docker-socket-proxy` | Limits portal-api to specific Docker API verbs (CONTAINERS, NETWORKS, POST, DELETE). Stable; rare releases. |
| `runtime-api-socket-proxy`, `vexa12-transcription-proxy` | Bridges the two deliberately narrow TCP capabilities: Vexa runtime's Unix Docker socket expectation, and bot-network STT traffic to the host tunnel. Keep current with Alpine socat releases because old tags carry many fixed base-package CVEs. |
| `garage` | S3-compatible object storage. Config field names change between minor releases — re-verify `garage.toml` after each bump. See `.claude/rules/klai/platform/garage.md`. |
| `listmonk` | Self-hosted mailing platform at `mailing.getklai.com` for campaign templates, lists, and Twenty-selected audiences. Upgrade after checking listmonk release notes and database migrations. |

### Pinned with known upstream gap

| Service | Why stuck |
|---|---|
| `firecrawl-rabbitmq` | Moved from 3.13 to the 4.x line on 2026-10-22. Firecrawl talks AMQP 0-9-1 through amqplib, which 4.x still serves; its durable queues are quorum queues and its per-listener queues are exclusive classic ones, so neither the removal of classic mirrored queues nor the `transient_nonexcl_queues` deprecation touches it, and its prefetch is per-consumer, not the denied `global_qos`. 4.3+ only upgrades in place from 4.2, which does not matter here: the container has no named volume and a new node name on every recreate, so the queue state is discarded by design. `deploy/tests/firecrawl-contract.sh` reads the pin from compose. |

---

## Dev stack — `docker-compose.dev.yml`

Use the same versions as your deployed environment to catch version-related
issues locally.

| Service | Notes |
|---|---|
| `postgres` | Same as the core stack. |
| `redis` | Aligned with the core stack. |
| `mongodb` | Same as the core stack. |
| `meilisearch` | Aligned with the core stack; keep `MEILI_DB_PATH=/meili_data` in dev as well. |
| `litellm` | Same as the core stack. |

---

## Application dependencies

### Python packages with version upper bounds

| Package | Service | Constraint | Why |
|---|---|---|---|
| `procrastinate>=3.0,<4` | knowledge-ingest | `<4` | Major version bump would require DB schema migration. API unchanged between 2.x and 3.x (PsycopgConnector, open_async(), run_worker_async()). |
| `graphiti-core>=0.28,<0.30` | retrieval-api | `<0.30` | 0.30.x is pre-release with breaking API changes. When 0.30.x stabilises on PyPI, test retrieval-api against it and remove the upper bound. |
| `icalendar>=6.1,<8.0` | portal-api | `<8.0` | Defensive — calendar parsing is brittle. Upper bound prevents surprise 8.0 breakage until we can validate. |
| `prometheus-client>=0.21,<1.0` | portal-api | `<1.0` | 1.0 release expected to change metric registry semantics. |

### Python runtime

The `python` slim base image, one Python minor across all internal services (the version lives in the Dockerfiles). See `docs/runbooks/version-management.md` §3.6 for the upgrade procedure (5 files must change in lock-step).

---

## Verification

To audit drift between this file and a running server, run on core-01:

```bash
docker ps --format '{{.Names}}\t{{.Image}}' | sort
```

Compare against the compose files, not against this document: it states no versions. A service that is held back or needs a pre-bump check gets a row here; `deploy/check-versions-doc.py` fails if a row names a service compose does not have.

---

## Automated CVE scanning

Every pinned image in compose is scanned weekly for CRITICAL/HIGH CVEs by `.github/workflows/scan-pinned-images.yml`. Findings land in the [Security tab → Code scanning](https://github.com/GetKlai/klai/security/code-scanning). Renovate opens PRs when newer upstream image tags exist; Trivy findings in third-party images that are already on the latest stable tag require upstream rebuilds, Klai-owned derived images, or documented temporary acceptance.

See `docs/runbooks/version-management.md` §9 for the full CVE detection stack.

