# SPEC: Klai Knowledge — Improvements Backlog

> Status: Backlog — low priority, no active sprint
> Architecture reference: `claude-docs/architecture/klai-knowledge-architecture.md`
> Parent spec: `claude-docs/specs/klai-knowledge-implementation.md` (complete)
> Created: 2026-03-22

---

## O2 — Sparse embeddings (BGE-M3 SPLADE)

**Priority:** LOW
**Trigger:** Retrieval quality issues reported, or document count > 1,000 per org

### Background

Current retrieval uses dense-only vectors (BAAI/bge-m3, 1024 dims) via TEI. BGE-M3 also produces SPLADE sparse vectors for keyword-based hybrid retrieval. Hybrid (dense + sparse) improves precision on exact-term queries (product names, error codes, acronyms).

### What to build

- Extend TEI / embedding pipeline to also produce SPLADE sparse vectors
- Add a `sparse` vector field to the `klai_knowledge` Qdrant collection
- Update `knowledge-ingest` to upsert both dense and sparse vectors
- Update `knowledge-ingest` retrieve endpoint to use hybrid search (RRF fusion)

### Acceptance criteria

- `POST /ingest/v1/document` upserts both dense and sparse vectors
- `POST /knowledge/v1/retrieve` uses Qdrant hybrid search with RRF
- Retrieval quality tested on a corpus of >100 documents
- No regression on existing dense-only queries

---

## O3 — Zod validation for `klai-docs` frontmatter PUT handler

**Priority:** LOW
**Location:** `klai-docs/app/api/orgs/[org]/kbs/[kb]/pages/[...path]/route.ts`

### Background

The PUT handler for page updates currently casts `extraFm` fields without Zod validation. Invalid frontmatter values (wrong type, missing required fields) are silently accepted and written to Gitea.

### What to build

- Define a Zod schema for `KnowledgeFrontmatter` in `klai-docs/lib/markdown.ts`
- Apply schema validation in the PUT handler before writing to Gitea
- Return `400 Bad Request` with a descriptive error if validation fails

### Acceptance criteria

- Valid frontmatter passes through unchanged
- Invalid types (e.g. `visibility: 123`) return `400` with field-level error message
- Schema defined in one place and reused by both type inference and runtime validation

---

## O6 — Helpdesk transcript adapter

**Priority:** LOW
**Status:** Blocked on PII detection
**Blocker:** Presidio (or equivalent) not built; GDPR-sensitive data requires redaction before indexing

### Background

`scribe-api` produces structured JSON transcripts from helpdesk calls. These transcripts are valuable for grounding the AI assistant on real support interactions. However, they may contain personal data (names, phone numbers, email addresses) that must be redacted before storing in Qdrant.

### Prerequisites

1. PII detection / redaction service (Presidio or custom) running on `klai-net`
2. GDPR review of transcript indexing (retention policy, right-to-erasure flow)

### What to build (once unblocked)

- `POST /ingest/v1/transcript` endpoint in `knowledge-ingest`
- Accepts scribe-api transcript JSON
- Runs PII redaction before chunking
- Chunks and indexes to Qdrant under the org's `kb_slug`

### Acceptance criteria

- PII redaction tested against a sample of real transcripts (with consent)
- No PII present in Qdrant after indexing (spot-check)
- Retention / deletion hook: when transcript deleted in scribe-api, chunks removed from Qdrant

---

## S1 — Authenticatie op knowledge-ingest endpoints

**Priority:** MEDIUM
**Trigger:** Zodra een extra service op klai-net wordt toegevoegd, of bij SSRF-risico-assessment

### Achtergrond

`/ingest/v1/*` en `/knowledge/v1/*` hebben geen authenticatie. De service is alleen bereikbaar via intern Docker network (`klai-net`), niet via Caddy. Het risico is acceptabel zolang het netwerk klein en gesloten is. De `/ingest/v1/crawl` endpoint is het gevaarlijkst: een gecompromitteerde container kan de ingest-service als SSRF-proxy gebruiken om interne HTTP-endpoints te benaderen.

### Wat te bouwen

- Voeg een `X-Service-Token` header check toe op alle routes (gedeeld secret via env var `KNOWLEDGE_SERVICE_TOKEN`)
- Aanroepers: klai-docs (KB creation webhook), LiteLLM hook (retrieve), Gitea webhook
- Uitzondering: `/health` blijft open

### Acceptance criteria

- Requests zonder correct token krijgen `401 Unauthorized`
- Alle bestaande aanroepers zijn bijgewerkt met de header
- `KNOWLEDGE_SERVICE_TOKEN` staat in `.env.sops`

---

## S2 — Gitea webhook signature-verificatie

**Priority:** MEDIUM
**Trigger:** Voor productie-gebruik met meerdere tenants

### Achtergrond

De `/ingest/v1/webhook/gitea` endpoint verifieert geen `X-Gitea-Signature` header. Elke service op klai-net (of een aanvaller met netwerktoegang) kan een nep-push event sturen en willekeurige content in een KB injecteren.

### Wat te bouwen

- Bij webhook-registratie in `klai-docs/lib/gitea.ts`: voeg `secret` toe aan de webhook config (random token per repo, of één gedeeld secret)
- In `knowledge-ingest/routes/ingest.py` gitea_webhook handler: verifieer `X-Gitea-Signature-256` HMAC-SHA256 header
- Gedeeld secret via env var `GITEA_WEBHOOK_SECRET`

### Acceptance criteria

- Webhooks met ongeldige of ontbrekende signature worden geweigerd met `401`
- Gitea stuurt de juiste signature (verifieerbaar via Gitea webhook delivery logs)
- Gedeeld secret staat in `.env.sops`

---

## S3 — SSL-verificatie in crawl endpoint

**Priority:** LOW
**Status:** Bekende workaround — `verify=False` in `crawl.py`

### Achtergrond

`httpx` in de Alpine-container kan HTTPS-certificaten niet verifiëren (SSL chain issue: `unable to get local issuer certificate`). Tijdelijke fix is `verify=False`. Dit betekent dat de crawl-endpoint vatbaar is voor MITM-aanvallen bij het fetchen van externe URLs. Voor het crawlen van publieke webcontent (geen credentials verstuurd) is het risico laag.

### Wat te bouwen

- Diagnose: bepaal welke CA root ontbreekt in de Alpine image
- Optie A: mount een custom CA bundle via Docker volume
- Optie B: gebruik `certifi` als `verify=certifi.where()` (werkt als certifi de juiste root bevat)
- Optie C: switch van Alpine naar Debian-slim base image (rijkere CA store)

### Acceptance criteria

- `verify=False` is verwijderd uit `crawl.py`
- `curl https://example.com` slaagt vanuit de container
- Geen regressie op bestaande ingest functionaliteit

---

## Out of scope (separate SPECs when needed)

- Gap detection (Phase 4) — requires >50 indexed documents per org and retrieval confidence data
- Graph layer (§5.3) — deferred pending query analysis
- BERTopic taxonomy discovery — needs ~1,000 documents
- Argilla review queue — needs taxonomy
- Cross-org federation (V2)
