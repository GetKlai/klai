# SPEC: Klai Knowledge — Implementation Plan

> Status: ✅ DONE — all core phases complete (2026-03-22)
> Architecture reference: `docs/architecture/klai-knowledge-architecture.md`
> Remaining low-priority items: `.moai/specs/SPEC-KB-BACKLOG/spec.md`
> Last updated: 2026-03-22

---

## Deployment notes (learned during implementation)

- `deploy.sh` only syncs `.env` files — docker-compose.yml and service code must be `scp`'d separately
- `knowledge-ingest` lives in `klai-infra/core-01/knowledge-ingest/` (local build via `docker compose build`)
- Alembic runs as `portal_api` role which does not own tables — DDL must run as `klai` superuser directly
- LiteLLM resolves custom loggers at `/app/{module_name}.py` — PYTHONPATH is ignored for module discovery
- Gitea requires `ALLOWED_HOST_LIST` in `app.ini` `[webhook]` section to allow internal Docker network webhooks
- Gitea org `description` field is used to store the Zitadel org ID (tenant scope for Qdrant)
- LiteLLM callbacks must be registered as module-level instances, not class references. Use `callbacks: [module.instance_name]` not `callbacks: [module.ClassName]`. Class references cause `TypeError: method() missing self` on post-call hooks.
- `sops.exe` is at `C:\Users\markv\sops.exe` (not on PATH)

---

## Phase 0 — Foundation ✅ DONE

### 0A — `PageFrontmatter` type in klai-docs ✅
- `KnowledgeFrontmatter` type added, intersected into `PageFrontmatter` in `klai-docs/lib/markdown.ts`
- All fields optional — existing pages unaffected
- Zod validation of `extraFm` not yet done (deferred)

### 0B — PostgreSQL knowledge schema migration ✅
- `klai-infra/core-01/postgres/migrations/001_knowledge_schema.sql` created and applied
- Tables live: `knowledge.artifacts`, `knowledge.derivations`, `knowledge.entities`, `knowledge.artifact_entities`, `knowledge.embedding_queue`

### 0C — Personal KB auto-provisioning in portal-api ✅
- `klai-portal` provisioning flow now creates LiteLLM team + scoped key (Step 2) and personal KB via klai-docs API (Step 5)
- `litellm_team_key` column added to `portal_orgs` (migration `l2m3n4o5p6q7`, applied out-of-band, stamped)
- `DOCS_INTERNAL_SECRET` added to both containers and `.env.sops`
- Existing tenants: no backfill needed (only 1 test environment, `getklai`)

---

## Phase 1 — Infrastructure ✅ DONE

### 1A — Qdrant ✅
- Running on `klai-net` at `http://qdrant:6333`
- `QDRANT_API_KEY` in `.env` and `.env.sops`

### 1B — Embeddings ✅ (decision: use existing TEI)
- FlagEmbedding standalone service: **not built**
- Decision taken: use existing `tei` container (BAAI/bge-m3, 1024 dims dense) for Phase 2
- Sparse embeddings deferred — dense-only retrieval is sufficient for current scale
- Revisit when retrieval quality issues appear or document count > 1,000

---

## Phase 2 — Unified Ingest API ✅ DONE

**Location:** `klai-infra/core-01/knowledge-ingest/`

### What was built

| Component | Status |
|---|---|
| `POST /ingest/v1/document` | ✅ chunk → embed → upsert |
| `POST /ingest/v1/webhook/gitea` | ✅ push event → fetch file → ingest |
| `POST /knowledge/v1/retrieve` | ✅ dense vector search, scoped by org_id |
| `GET /health` | ✅ |
| Qdrant collection `klai_knowledge` | ✅ created on startup, indexed on `org_id`, `kb_slug`, `artifact_id`, `content_type`, `user_id` (idempotent — missing indexes added on restart) |
| Gitea webhook on `helpcenter` repo | ✅ configured, tested end-to-end |
| Gitea `org-getklai` description | ✅ set to Zitadel org ID `362757920133283846` |

### What was NOT built (deferred)
- Contextual Retrieval prefix generation (Mistral Small 3.1) — not needed at current scale
- HyPE question generation — not needed at current scale
- BGE-M3 sparse embeddings — deferred (dense sufficient for now)
- `knowledge.artifacts` PostgreSQL writes — Phase 2 uses Qdrant as sole source of truth; semantic graph layer is Phase 4+
- Backfill CLI — not needed (no existing KB articles in test environment)

### Chunking strategy
Markdown-aware heading-based chunker (pure Python, no docling for `.md` files).
Docling is available for future non-markdown formats.

---

## Phase 3 — LiteLLM hook ✅ DONE

### 3A — KlaiKnowledgeHook ✅
- `klai_knowledge.py` deployed, mounted at `/app/klai_knowledge.py` in LiteLLM container
- Registered via `litellm_settings.callbacks` in `config.yaml`
- Graceful degradation: any failure → log warning → return data unchanged
- Trivial message filter active (NL + EN greetings, messages < 15 chars)

### 3B — LiteLLM team keys per tenant ✅ (new tenants)
- New tenants get a per-tenant LiteLLM team + scoped key at provisioning
- Key carries `org_id` in metadata → hook uses this for Qdrant scope
- **Existing tenant (`getklai`):** still uses master key → hook skips (no `org_id` in metadata) → no retrieval injected

---

## Phase 3 — Remaining: existing tenant key migration ✅ DONE

The `getklai` test environment (https://getklai.getklai.com) was migrated from the LiteLLM master key to a scoped team key:

- Scoped LiteLLM team key created: `sk-bNLQ61Qs533P7GOzfXdyxA` with `org_id: "362757920133283846"` in metadata
- Key set as `LITELLM_API_KEY` in `/opt/klai/librechat/getklai/.env`
- End-to-end retrieval verified working for `getklai`

---

## Phase 4 — Gap detection (requires: Phase 3 + meaningful indexed content)

Deferred. Prerequisites:
- Phase 3 tenant key migration complete for at least one org ✅ — `getklai` migration done, retrieval verified
- At least one org with > 50 indexed documents (retrieval confidence data needed)
- Separate SPEC required

---

## Phase 5 — Enrichment adapters (parallel, requires: Phase 2)

| Adapter | Input | Status | Notes |
|---|---|---|---|
| Web crawl | URLs | ✅ Done — `POST /ingest/v1/crawl` in knowledge-ingest | html2text; verify=False (SSL chain issue on core-01) |
| Helpdesk transcript | JSON transcripts from scribe-api | ⛔ Blocked | PII detection (Presidio) not built; GDPR-sensitive |
| Focus "Save to Knowledge" | Focus session synthesis | 🔜 Deferred | Focus rewrite not started |

---

## Open items — all resolved

| # | Item | Status |
|---|---|---|
| O1 | Existing tenant (`getklai`) LiteLLM key migration | ✅ Done |
| O4 | Gitea webhook for `personal` KB repos | ✅ Done |
| O5 | Web crawl enrichment adapter | ✅ Done |
| O2 | Sparse embeddings | moved → `klai-knowledge-improvements.md` |
| O3 | `klai-docs` Zod frontmatter validation | moved → `klai-knowledge-improvements.md` |
| O6 | Helpdesk transcript adapter | moved → `klai-knowledge-improvements.md` |

---

## What is NOT in scope for this plan

- Graph layer (deferred pending query analysis — §5.3)
- BERTopic taxonomy discovery (needs ~1,000 documents first)
- Argilla review queue (needs taxonomy)
- Cross-org federation (V2)
- Bi-temporal V2 features (transaction-time audit trail, temporal joins)
- PII detection / Presidio (required before transcript extraction goes to prod)
