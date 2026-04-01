# Klai Platform: Technical Decisions

*Last updated: 2026-04-01. For the Knowledge product specification, see [klai-knowledge-architecture.md](../klai-knowledge-architecture.md).*

## Stack

| Layer | Choice | Why not alternatives |
|---|---|---|
| Customer portal frontend | Vite + React SPA + Tailwind v4 | Authenticated app — no SSR needed; Voys-UI (Cleopatra) works directly |
| Routing (portal) | TanStack Router (code-based) | Type-safe routes and search params; React Router v7 as backup for simple navigation |
| Data fetching (portal) | TanStack Query | Dashboard + mutations need caching and retry; SWR as alternative for simpler use |
| Customer portal backend | FastAPI (Python) | One language for all backend services; provisioning, billing, Scribe, later RAG |
| Billing | Mollie + custom module | External billing platforms are overkill for these requirements |
| Chat UI | LibreChat (no fork) | Custom UI only when LibreChat demonstrably blocks us |
| Auth / Identity | Zitadel | Native B2B multi-tenancy, lightweight (Go binary) |
| Model Proxy | LiteLLM OSS -> Enterprise | Start free, upgrade at first revenue for audit trail |
| Model routing | LiteLLM Complexity Router | Built into LiteLLM OSS; zero external calls, <1ms overhead; 4 tiers by query complexity |
| Reverse proxy | Caddy (core-01) | Better LLM streaming than Traefik, lighter, easier to debug |
| Infra (public-01) | Coolify on Hetzner CX42 | Delay Kubernetes as long as possible (autoscaling = complexity) |
| Infra (core-01) | Direct Docker on EX44 | Coolify's reverse proxy conflicts with Caddy; container lifecycle via custom provisioning service |

## Customer Portal

Customers sign up, log in via Zitadel, and are redirected to their LibreChat environment. Billing module handles:
- Monthly direct debit via Mollie (mandate at signup)
- Self-service: update company name and VAT number
- Request PDF invoice (generated from Mollie data)

## Multi-tenancy model

Per customer: own subdomain (`company.getklai.com`) with the full Klai application. Inside: Chat, Usage, Settings, Invoices. LibreChat runs as a feature within the application, not as the main page.

Technical: one Zitadel Organization + one LibreChat container + one LiteLLM virtual key per customer. Wildcard DNS (`*.getklai.com`) + Caddy handles routing automatically.

Creating a customer = automated via APIs (Zitadel + Docker + LiteLLM). No manual work.

Architecture principle: tenant = isolation layer. Everything is stored and tracked per user, not per company. Data, usage, voice, history — everything belongs to the individual user.

## Architecture separation: public vs. AI stack

Two layers, two machines, never mixed:

- **public-01 (Coolify)** — website, CRM, feedback, status page. Coolify manages routing.
- **core-01 (Caddy)** — AI stack: LiteLLM, LibreChat, customer portal, all `*.getklai.com` subdomains.

## Server layout (Phase 1 complete — 2026-03)

**Phase 1 complete.** First paying customer live, Portal API in production, provisioning active.
For the authoritative and current service list per server, see `klai-infra/SERVERS.md`.

**Current servers:**

| Server | Type | Cost | Services |
|---|---|---|---|
| public-01 | CX42 — Hetzner HEL | €17/mo | Coolify, website, Twenty (CRM), Fider, Uptime Kuma |
| core-01 | EX44 — Hetzner HEL | €47/mo | Caddy, Zitadel, MongoDB, Meilisearch, LiteLLM + Mistral API, Ollama (fallback), LibreChat containers, PostgreSQL, Redis, Qdrant, VictoriaMetrics, VictoriaLogs, Grafana, Alloy, cAdvisor, Portal API, klai-mailer, GlitchTip, scribe-api, docling-serve, SearXNG, research-api, knowledge-ingest, retrieval-api, klai-knowledge-mcp |
| gpu-01 | GEX44 + RTX 4000 Ada 20GB — Hetzner FSN | — | TEI (BGE-M3 dense, :7997), Infinity (reranker, :7998), bge-m3-sparse (:8001), whisper-server (:8000) — reached from core-01 via SSH tunnel at 172.18.0.1 |
| monitor-01 _(planned)_ | CAX11 — Hetzner HEL | €5/mo | Dedicated VictoriaMetrics + VictoriaLogs + Grafana (currently co-hosted on core-01) |

EX44 is production-ready from day one (64 GB RAM, dedicated hardware). No migration needed as we grow. ai-01 (GPU) follows at Phase 3 trigger.

**Phase 3+ (self-hosting AI):**

| Server | Type | Cost | Services |
|---|---|---|---|
| core-01 | EX44 — Hetzner HEL | €47/mo | All app services (unchanged) |
| ai-01 | H100 80GB — Nebius HEL | €1,100-1,950/mo | vLLM, Whisper, LLM inference |
| monitor-01 | CAX11 — Hetzner HEL | €5/mo | VictoriaLogs + VictoriaMetrics + Grafana |

**Total Phase 3: ~€1,150-2,000/mo**

Principle: public-01 and core-01 share no ports and no machines. monitor-01 receives logs from all servers.

**Phase 0 LiteLLM failover:** primary Mistral API, fallback Ollama on core-01. Automatic via LiteLLM fallback configuration.

**Phase 3+ networking (when self-hosting AI):** core-01 (Hetzner HEL) and ai-01 (Nebius HEL) are in the same city — latency 5-15 ms RTT. Connected via WireGuard tunnel. LiteLLM calls vLLM via private WireGuard IP. Failover to Scaleway Paris (H100) or RunPod EU if Nebius goes down. Nebius SLA: 99.9%, has official OpenTofu provider.

**Klai Knowledge (Phase 2 — deployed March 2026):** Qdrant on core-01 (`klai_knowledge` + `klai_focus` collections). Custom `knowledge-ingest` service replaces rag_api. `retrieval-api` serves hybrid RRF search. `bge-m3-sparse` sidecar handles sparse embeddings via FlagEmbedding. pgvector no longer used for vector search.

## Branding

No fork. Logo via Docker volume mount (`client/dist/assets/logo.svg`). Browser tab title via `client/dist/index.html`. LibreChat default colors and layout acceptable in Phase 0. Zitadel login screen fully branded via LabelPolicy.

Adjusting colors and layout requires building from source — not a priority as long as the customer portal (custom React) is the primary branded environment.

## LibreChat: technical details

**Architecture model: silo per tenant.** One LibreChat container per customer. Benefits: full data isolation at storage level, per-customer `librechat.yaml` with own model settings, independent rollback per customer. Updates are not manual work: one Docker image for all containers, rolling restart via script (one command, not N manual actions).

OIDC configuration is in `.env` (not in `librechat.yaml`). Zitadel integration via Authorization Code + PKCE. Roles are passed from Zitadel token to LibreChat for access control.

**Database per tenant:** shared MongoDB server, separate database per tenant via `MONGO_URI` (e.g. `mongodb://mongo/tenant_abc`). Database-level isolation without a MongoDB container per customer. One MongoDB process, N databases — standard pattern for multi-tenant MongoDB deployments.

**Meilisearch:** one shared instance for all tenants. LibreChat filters on `userId` (globally unique via Zitadel). See Compatibility Review for details.

Resource per LibreChat container (without own Meilisearch): ~250-350 MB RAM idle. Core-01 (EX44, 64 GB) has room for 50+ tenants on a RAM basis.

Usage reporting (token usage per user/company) does not exist natively in LibreChat. Built via the PostgreSQL transactions table in the customer portal. LibreChat 2026 roadmap includes an Admin Panel but it won't offer per-organization reporting.

## Phases

| Phase | Trigger | What's added | Status |
|---|---|---|---|
| 0 | Initial setup | Caddy + Zitadel + MongoDB + LiteLLM OSS + Mistral API + Ollama fallback + LibreChat + Alloy + VictoriaLogs | **Done** |
| 1 | First paying customer | Customer portal live, Mollie direct debit active, provisioning service, VictoriaMetrics + Grafana | **Done** (2026-03) |
| 2 | Customers request documents | Klai Knowledge: knowledge-ingest + Qdrant + retrieval-api + bge-m3-sparse + LiteLLM hook (feature gate + gap detection) + klai-knowledge-mcp (personal + org KB writes) | **Done** (2026-03) — retrieval live, getklai tenant verified end-to-end |
| 3 | Break-even self-hosting (~30K active users) | ai-01 GPU server, vLLM, Whisper (GPU) — model choice Qwen3-32B + Qwen3-8B | In progress |
| 4 | First enterprise customer | SAML SSO per org, SCIM provisioning | Planned |
| 5 | Audit logs or >5 admins needed | LiteLLM Enterprise | Planned |

## Scribe (speech-to-text)

Added early, already working in another stack. UI likely custom-built. Architecture principle: voice data is per user, not per company. No sharing between users.

**Deployment (updated 2026-03):** `scribe-api` runs on core-01 and calls `whisper-server` on gpu-01 via SSH tunnel (`http://172.18.0.1:8000`). GPU migration completed with SPEC-GPU-001/SPEC-DEVOPS-002. No env change needed when scaling to a larger GPU — just update the tunnel config.

**Whisper model (Phase 3+):** faster-whisper large-v3-turbo, INT8 quantization. ~2-2.5 GB weights, ~4-6 GB VRAM total. 6x faster than large-v3, multilingual (NL/DE/EN), less than 1-2% quality loss. Deployed on ai-01 alongside vLLM — ~34 GB VRAM free, no conflict. NVIDIA MPS with `CUDA_MPS_ACTIVE_THREAD_PERCENTAGE=20` prevents SM contention.

**Scaling trigger:** dedicated GPU (L4, ~€0.50/hr) when more than 5 concurrent audio streams or hard p99 SLO <500ms required. At that usage level there is also revenue to justify it.

## Privacy-first principle

All data stays within the EU. No closed or proprietary AI APIs from non-EU companies (OpenAI, Anthropic, Google, etc.) — exclusively open source or open weight models. This is a core value, not a technical choice.

**Managed vs self-hosted**: the principle applies to the model, not necessarily to the hosting. An open weight EU model offered by the original European company via API is consistent with this core value. The ambition is full self-hosting when cost-effective, but an EU-managed API based on open weight models is an acceptable intermediate step.

## AI Models — Hosting Strategy

### Approach: start with API, evaluate self-hosting

**Phase 0 — Managed API (Mistral)**
LiteLLM points to the Mistral API. Mistral is a French company, EU infrastructure by default, open weight models, DPA available, no training on customer data. Costs are variable and low at early user numbers.

Fallback: small open source model via Ollama on core-01 (CPU). On Mistral failure, LiteLLM switches automatically — users notice a delay, no data loss, no downtime.

**Trigger for self-hosting**: when the fixed cost of a GPU server is lower than Mistral API costs. Break-even at ~1.2 billion tokens per month (~30,000 active users at normal business usage).

**Model choice**: out of scope for now. Determined at the point when self-hosting is the right step and the model landscape is re-evaluated.

**Server layout at self-hosting**: ai-01 (GPU server) is added only at the self-hosting trigger. Until then, no ai-01.

---

## AI Models — Model Overview (reference for self-hosting phase)

### Models evaluated

| Model | Params | VRAM FP8 | Context | License | Advantages | Disadvantages | Status |
|---|---|---|---|---|---|---|---|
| **Qwen3-8B** | 8B | ~9 GB | 32K | Apache 2.0 | Fast, low power, cheap per token | Less suited for complex reasoning | **Chosen: fast model** |
| **Qwen3-32B** | 32B | ~33 GB | 32K | Apache 2.0 | Best reasoning in class, 2,352 tok/s (confirmed), 119 languages | Context 32K is the limit for long documents | **Chosen: primary model** |
| **Mistral Small 3.2** | 24B | ~28 GB | 128K | Apache 2.0 | 128K context, French company (EU), stable function calling | Weaker reasoning (46% vs 62%+ GPQA), slower than 8B | Standby: trigger = 32K context complaint |
| **Magistral Small** | 24B | ~24 GB | 128K | Apache 2.0 | Reasoning specialist (RL-trained), 70.7% AIME2024 | Reasoning already covered by Qwen3-32B thinking mode | Not needed with current setup |
| **Llama 3.3 70B FP8** | 70B | ~75 GB | 128K | Llama 3 | High quality, strong on NL/DE | Doesn't fit on H100 with usable KV cache (5 GB remaining = max 3-5 concurrent users) | Excluded: not viable |
| **Llama 3.3 70B INT4** | 70B | ~45 GB | 128K | Llama 3 | Fits on H100, 10-20 concurrent users | Quality loss from quantization, no dual-model room | Excluded: prefer FP8 quality |
| **Mistral Large 3** | 675B MoE | ~90 GB FP8 | 128K | Apache 2.0 | Frontier quality, open weights | Doesn't fit on 1 H100 (minimum 2x H100 needed) | Excluded: infrastructure too heavy |
| **EuroLLM 22B** | 22B | ~11 GB | 32K | Apache 2.0 | All 24 EU languages, explicitly EU-trained | Less strong than Qwen3-32B on reasoning | Future: specialist for government/EU languages |
| **DeepSeek V3.2** | 671B MoE | 10-16x H100 | 128K | MIT | Frontier quality | Requires 10-16x H100 — not realistic | Excluded: infrastructure requirements |
| **Mixtral** | MoE | - | - | Apache 2.0 | Once strong for multilingual | Overtaken by Qwen3 and Mistral Small 3.2 | Excluded: outdated |
| **Gemma 3** | various | - | - | Custom | Good performance | Google retains right to remotely stop deployment | Excluded: incompatible with privacy-first |
| **LLaMA 4** | various | - | - | Custom | Multimodal, frontier quality | EU usage restricted in license (multimodal model) | Excluded: license restriction |
| **GPT-NL** | unknown | n/a | - | Government | Dutch-trained, high NL quality | Weights not public, government pilot phase | Deferred: reconsider end 2026/2027 |
| **Qwen2.5** | various | - | - | Apache 2.0 | Stable, widely used | Superseded by Qwen3 (April 2025), no longer current | Excluded: outdated |

### Choice: Qwen3-32B + Qwen3-8B

Two models on one H100 80GB, routed via LiteLLM Complexity Router.

**Complex: Qwen3-32B FP8** — reasoning, analysis, code, multi-step questions
- VRAM: ~33 GB | Context: 32K | Throughput: 2,352 tok/s (confirmed benchmark)

**Fast: Qwen3-8B FP8** — short questions, summarization, standard tasks
- VRAM: ~9 GB | Significantly higher throughput, ~4x lower energy per token

VRAM distribution: 42 GB weights, ~25-30 GB KV cache (after CUDA overhead). vLLM as two separate instances with `--gpu-memory-utilization` split (~0.55 + ~0.40).

**Why dual-model and not one large model:**
Research (OpenAI enterprise data, 2025) shows that 60-70% of AI queries are simple: short answers, standard formulations, quick tasks. Sending those to a 32B model wastes power, VRAM and money. The 8B is faster for those tasks and better for the customer experience. Only complex queries justify the heavier model.

**Why Qwen3 over Mistral:**
Mistral Small 3.2 scores 46% on GPQA Diamond, Qwen3-32B 62%+. That difference is noticeable in reasoning, analysis and code. The "European company" argument (Mistral is French) doesn't factor in: for Klai only where the AI runs matters, not who made the weights. Mistral Small 3.2 stays on standby if the 32K context limit becomes a practical problem.

**Routing:** LiteLLM Complexity Router scores each query on 7 dimensions (code, reasoning markers, technical terms, token count, etc.) and routes in <1ms without an external API call. LibreChat always sends the full conversation history — context loss on model switch is not possible.

**Concurrent users:** AI chat tools score 3-5% concurrent users at peak (vs. generic SaaS 2-3%). Confirmed by ChatGPT enterprise data: 4.5 sessions/day, 14 min/session, DAU/MAU 40-68%. At 1,000 registered users: 30-50 concurrent at peak. H100 with dual-model routing handles this comfortably.

**Context 32K:** Sufficient for 80-90% of business tasks (emails, reports, contracts up to ~30 A4 pages). Phase 2 (RAG) structurally solves long documents. Trigger for Mistral switch: demonstrable customer complaints about document limits.

**Scaling path:** Second H100 on growth. Multi-server with more specialized models when usage justifies it.

GPT-NL: deliberately deferred. Government pilot phase, weights not public. Reconsider end 2026/2027.

### Infrastructure cost per user

Fixed infrastructure: ~€1,570/mo (H100 €1,500 + servers €70). H100 capacity with dual-model routing: 600-1,500 registered users comfortably.

| Users | Infra/user/mo | Note |
|---|---|---|
| 100 | €15.70 | Startup phase, infra dominates |
| 250 | €6.28 | Early customers |
| 500 | €3.14 | Approaching break-even |
| 750 | €2.09 | Second H100 approaching |
| 1,000 | €1.57 | First H100 full (comfortable) |
| 1,500 | €1.05 | Second H100 needed, costs halve after |

These are purely infrastructure costs. Margin, development and support are on top.

## Knowledge System Design Principles

> Research session 2026-03-25. Full empirics and source citations: [research/knowledge-system-fundamentals.md](research/knowledge-system-fundamentals.md)

These principles are empirically derived from studying 8 independent knowledge systems built between 2001 and 2024. They directly shaped the Klai Knowledge architecture (Phase 2).

### Five universal entity types

Eight independent systems — from Wikipedia to enterprise knowledge graphs to recent AI-memory systems — converged on the same five categories without coordination:

| # | Type | Examples |
|---|---|---|
| 1 | People and organizations | User, company, customer, team |
| 2 | Documents and messages | Email, manual, decision, Slack message |
| 3 | Containers | Project, department, folder |
| 4 | Events | Meeting, conversation, deployment |
| 5 | Categories | "Billing", "Support", taxonomy labels |

A sixth type — **Decision** — is added as the intentional layer: capturing *why* something was decided, not just what exists.

Six universal relationships cover all organizational knowledge: `created`, `owns`, `contains`, `linked_to`, `classified_as`, `member_of`.

### Quality is determined at ingest, not retrieval

The most consequential finding: adding contextual metadata at storage time reduces retrieval errors by 49% (Anthropic research). No retrieval technique compensates for a poor ingest. The moment something is ingested is the most important moment in the system.

**In Klai:** `knowledge-ingest` performs contextual enrichment (HyPE question generation, content profile metadata, source context) before storage — not as post-processing. See [knowledge-ingest-flow.md](knowledge-ingest-flow.md).

### Hybrid processing: 90% automatic, 10% human review

Fully automated extraction produces 20–45% errors on standard business documents. The hybrid approach: 90% of documents are processed automatically; only the 10–15% where the system itself has low confidence are flagged for human review. Near-human quality at 500× less effort.

**In Klai:** `assertion_mode` is `shadow` in Phase 2. Full hybrid review is a Phase 3 target — tracked in `klai-knowledge-architecture.md §7.4`.

### Three storage layers — each wins at something different

| Layer | Technology | Wins at |
|---|---|---|
| Relational | PostgreSQL | Precise filtering: "all documents from team X, Jan–Mar" |
| Vector | Qdrant (BGE-M3 dense + sparse) | Semantic similarity: finds relevant results even when exact words differ |
| Graph | FalkorDB + Graphiti | Multi-hop relationships: "which decisions connect to this document?" |

All three are needed. The combination consistently outperforms any single approach. PostgreSQL and Qdrant are live; FalkorDB is deployed but not yet activated for retrieval (Phase 3).

### Build order (followed by Klai)

1. Define entity types and relationships — the universal core
2. Build taxonomy — categories as a hierarchy on top of the core
3. Ingest with context — enrichment at storage time; human review only for low-confidence items
4. Index taxonomy labels — integral to the search index, not post-processing
5. Combine at retrieval — SQL for filters, vector for semantics, graph for connections

**The classification at storage is primary. The retrieval technique is secondary.**

### Knowledge graph: FalkorDB + Graphiti (Phase 3)

FalkorDB is deployed on core-01 (not yet activated for retrieval). Key decisions:

- **FalkorDB** over Apache AGE: PostgreSQL major version upgrades break AGE — unacceptable for a long-lived system. Both are supported by Graphiti, migration minimal if ever needed.
- **Graphiti** for extraction: entity resolution, deduplication, and temporality handled automatically.
- **Temporal tracking at relationship level**: each edge has `valid_at`, `invalid_at`, `expired_at` — enables "what did the system know on date X?" Full details in the research doc.
- **reference_time rule**: when ingesting a historical document, pass the document date as `reference_time`, not today's date.
- **Density target**: ≥2 connections per node. Back-linking each entity to its source document achieves ~6 connections/node in production (HippoRAG2, independently verified).

### Retrieval enrichment: HyPE, not Contextual Retrieval

HyPE (Hypothetical Prompt Embeddings) and Contextual Retrieval solve different problems:

- **Contextual Retrieval** — adds surrounding context to each chunk at storage time. Solves chunk boundary loss.
- **HyPE** — generates hypothetical questions a chunk would answer. Solves the vocabulary gap between user queries and document language.

Klai uses HyPE because vocabulary gap is the primary retrieval problem for business knowledge. HyPE questions are stored as a separate index in Qdrant and contribute to hybrid RRF retrieval scoring via Dual-Index Fusion.

---

## RAG stack (Phase 2 — deployed March 2026)

> **Deployed state:** Custom `knowledge-ingest` + `retrieval-api` + Qdrant. LibreChat `rag_api` (Track A) was never deployed — the custom approach was built directly. Haystack/LlamaIndex was removed; the custom services cover all orchestration needs.

Two tracks were evaluated during planning; Track B was built directly.

### Track A: LibreChat rag_api (evaluated, not deployed)

LibreChat has a built-in RAG service (`rag_api`). Evaluated but not deployed. Downsides: LangChain lock-in (breaking changes), no Qdrant support, weak multi-tenant isolation (file_id, not tenant_id).

### Track B: custom RAG services (deployed as knowledge-ingest + retrieval-api)

Custom FastAPI services with full control. Haystack/LlamaIndex orchestration was removed — Qdrant + the ingest pipeline covered all orchestration needs directly.

```
Document parsing   docling-serve (MIT, IBM Research)
Embeddings dense   BGE-M3 via HuggingFace TEI
Embeddings sparse  BGE-M3 via FlagEmbedding sidecar (bge-m3-sparse, http://bge-m3-sparse:8001)
Vector store       Qdrant on core-01 (klai_knowledge + klai_focus collections)
Ingest service     knowledge-ingest (FastAPI, /ingest/v1/*)
Retrieval service  retrieval-api (FastAPI, POST /retrieve, 3-leg RRF fusion)
LiteLLM hook       KlaiKnowledgeHook (feature gate, user_id scoping, gap detection)
MCP server         klai-knowledge-mcp (save_personal_knowledge, save_org_knowledge)
```

### Shared components (both tracks)

**BGE-M3** (FlagOpen, MIT, 11.4k stars): only embedding model with MIT license + SOTA multilingual NL/DE + hybrid retrieval (dense + sparse). VRAM: 4-10 GB on H100 in production (due to CTranslate2 memory pools and batching). Via HuggingFace TEI as microservice.

**Docling** (IBM Research, MIT, 54.7k stars): PDF/DOCX/XLSX/PPTX parsing with AI layout analysis and table recognition. Has its own FastAPI REST wrapper. Fully local, no external calls.

**Exclusions:**
- Jina-embeddings-v3: CC BY-NC 4.0 — prohibited for commercial SaaS use
- PyMuPDF4LLM: AGPL-3.0 — requires open source publication of the full application for SaaS use
- LangChain as own choice: high churn (breaking changes), higher overhead; acceptable as rag_api dependency, not as own architecture choice
- pgvector for Track B: no namespace isolation, workarounds needed for multiple tenants

## Deliberate exclusions (reconsider only at trigger)

- LiteLLM Enterprise: only at first revenue (audit trail as product story)
- Kubernetes: delay as long as possible
- Custom chat UI: only after proof that LibreChat is a deal-breaker (the customer portal is custom React, that is a separate decision)
- Zustand: React Context sufficient for auth/user state that rarely changes; add when demonstrable performance problems arise
- Llama 3.3 70B FP8 on single H100: leaves only 5 GB KV cache, max 3-5 concurrent users — not viable
- DeepSeek V3.2 (671B): requires 10-16x H100, not realistic
- Mixtral: overtaken by Qwen3 and Mistral Small 3.2, not recommended for new setups
- Mistral Small 3.2: on standby if Qwen3-32B hits context limits (128K vs 32K, same H100)
- Gemma 3: Google retains right to remotely stop deployment — incompatible with privacy-first
- LLaMA 4: EU usage restricted in license (multimodal model)

---

## Backup

Three data layers, one storage level: **Hetzner Object Storage** (S3-compatible, HEL datacenter, GDPR-compliant, €0.024/GB/month). Outbound traffic from Hetzner servers to Hetzner Object Storage in the same datacenter is free.

### What gets backed up

**MongoDB (chat history, conversations, uploads)**
Most critical data. Unrecoverable if lost.
- Tool: `mongodump --gzip` to Hetzner Object Storage
- Frequency: nightly (daily at 02:00)
- Retention: 30 days daily dumps, 12 months monthly snapshots
- Restore per tenant: `mongorestore --db tenant_abc` — no other tenants affected

**PostgreSQL (portal data, billing, provisioning, LiteLLM usage)**
Invoice data and tenant registration live here.
- Tool: `pg_dump` compressed to Object Storage
- Frequency: nightly
- Retention: 30 days

**Tenant config files (.env, librechat.yaml per tenant)**
Generated at provisioning, can be regenerated from PostgreSQL. Backing up costs nothing significant.
- Tool: `tar.gz` of the config directory, to Object Storage
- Frequency: at every provisioning action + nightly

**Not backed up separately:**
- LibreChat Docker images (always re-pullable)
- Meilisearch indexes (automatically rebuilt from MongoDB on restart)
- VictoriaLogs/VictoriaMetrics data (monitoring is instrumental, not business-critical)

### Backup cost estimate

Estimates based on text data (chat history compresses heavily, gzip factor ~10x).

| Situation | Raw MongoDB data | After gzip | Retention 30 days | Object Storage/mo |
|---|---|---|---|---|
| 10 tenants (start) | ~5 GB/mo | ~0.5 GB/dump | ~15 GB | <€1 |
| 50 tenants | ~25 GB/mo | ~2.5 GB/dump | ~75 GB | ~€2 |
| 200 tenants | ~100 GB/mo | ~10 GB/dump | ~300 GB | ~€7 |
| 500 tenants | ~250 GB/mo | ~25 GB/dump | ~750 GB | ~€18 |

PostgreSQL dumps are negligibly small (<5 GB total for hundreds of tenants).

**Backup costs are < €20/month up to 500 tenants.** Never a factor in the cost picture — ai-01 dominates at €1,100-1,950/mo.

### Tooling

**Restic** (recommended): single binary, native S3 support, deduplication and encryption built in. Daily cronjob on core-01. Ref: [restic.net](https://restic.net)

Hetzner Object Storage credentials via environment variables, SOPS/age encrypted — same approach as other secrets in the stack.

---

## Compatibility Review (2026-03-03)

Independent research per component on standard integration and mutual compatibility.
Legend: ✅ confirmed compatible | ⚠️ attention point | ❌ correction needed

---

### Frontend: Vite + React + Tailwind v4 + TanStack

**Tailwind v4 + @tailwindcss/vite**
- ✅ Official first-party Vite plugin, replaces PostCSS setup. No tailwind.config.js needed.
- ⚠️ v4 generates many CSS custom properties on `:root`. Can conflict with variables from external UI libraries. Mitigation: `prefix()` import. Ref: [GitHub #15754](https://github.com/tailwindlabs/tailwindcss/issues/15754)
- Docs: [tailwindcss.com/docs](https://tailwindcss.com/docs)

**TanStack Router + TanStack Query**
- ✅ Standard combination. Router's loader API is explicitly designed for integration with Query for prefetched route state.
- ⚠️ With code-based routing no extra Vite plugin needed (only with file-based). TypeScript issue with `moduleResolution: bundler` + `composite: true` — adjust in tsconfig.json. Ref: [GitHub #2178](https://github.com/TanStack/router/issues/2178)
- Docs: [tanstack.com/router](https://tanstack.com/router/latest/docs/routing/code-based-routing)

**Voys-UI "Cleopatra"**
- ❌ No public npm package found. Voys GitHub has 6 public repos, no design system. Most likely internal/private library.
- ⚠️ Unknown whether it is React-compatible and whether it conflicts with Tailwind v4 CSS custom properties or global styles. Must be verified internally before the portal frontend is built.

---

### Auth: Zitadel + LibreChat + FastAPI + React SPA

**Zitadel: Authorization Code + PKCE**
- ✅ Standard recommended flow. Zitadel application type: "User Agent" with PKCE S256. LibreChat's `openid-client` handles PKCE automatically.
- Docs: [zitadel.com/docs/guides/integrate/login/oidc/oauth-recommended-flows](https://zitadel.com/docs/guides/integrate/login/oidc/oauth-recommended-flows)

**Zitadel: B2B Organizations**
- ✅ One Organization per customer is the canonical Zitadel approach, explicitly documented. Org routing via primary domain scope.
- Docs: [B2B solution scenario](https://zitadel.com/docs/guides/solution-scenarios/b2b)

**FastAPI JWT validation**
- ✅ Library available: `fastapi-zitadel-auth` (MIT, community). Validates via JWKS endpoint.
- Ref: [pypi.org/project/fastapi-zitadel-auth](https://pypi.org/project/fastapi-zitadel-auth/)

**React SPA tokens**
- ✅ `@zitadel/react` (wrapper around `oidc-client-ts`) or `react-oidc-context`. Both handle PKCE, token exchange and silent renewal.
- Docs: [zitadel.com/docs/examples/login/react](https://zitadel.com/docs/examples/login/react)

**Zitadel custom claims/roles to LibreChat**
- ✅ Supported via "Assert Roles on UserInfo" in project settings or via Zitadel Actions (JavaScript) for flat `groups` claim.
- Docs: [zitadel.com/docs/apis/openidoauth/claims](https://zitadel.com/docs/apis/openidoauth/claims)

**LibreChat OIDC: known issues (not Zitadel-specific)**
- ⚠️ `OPENID_REUSE_TOKENS=true` breaks existing users. Do not use unless fresh deployment. [GitHub #9303](https://github.com/danny-avila/LibreChat/issues/9303)
- ⚠️ Explicit setting required: `OPENID_USERNAME_CLAIM=preferred_username` otherwise LibreChat falls back to `given_name`. [GitHub #8672](https://github.com/danny-avila/LibreChat/issues/8672)
- ⚠️ Logout does not call Zitadel end-session endpoint. Zitadel session remains active after LibreChat logout. Building a custom logout flow is then necessary.

---

### LibreChat: Database and Provisioning

**Database: MongoDB is required**
- ❌ LibreChat requires MongoDB. There is no native PostgreSQL support.
- ✅ Alternative: FerretDB as MongoDB-compatible API on top of PostgreSQL (core-01 already has PostgreSQL). Documented as working with LibreChat by the FerretDB community. Less battle-tested than real MongoDB.
- ⚠️ Schema approach: one MongoDB database per tenant on a shared cluster (via MONGO_URI database name per container). No native multi-tenancy within one database instance.
- Ref: [blog.ferretdb.io/replacing-mongodb-with-ferretdb-librechat](https://blog.ferretdb.io/replacing-mongodb-with-ferretdb-librechat/)

**Provisioning API: not available**
- ⚠️ LibreChat has no admin REST API for user management, OIDC configuration or model endpoints. Admin Panel is on the [2026 roadmap](https://www.librechat.ai/blog/2026-02-18_2026_roadmap).
- Provisioning goes via: Docker API + templated `.env` and `librechat.yaml` per container. Scriptable but not via HTTP calls.

**Token usage to PostgreSQL**
- ⚠️ No built-in bridge. LibreChat stores transactions in MongoDB (Transactions collection). Export via: direct MongoDB query or [virtUOS/librechat_exporter](https://github.com/virtUOS/librechat_exporter) (Prometheus exporter). ETL from MongoDB to PostgreSQL for billing is custom work.
- Docs: [librechat.ai/docs/configuration/token_usage](https://www.librechat.ai/docs/configuration/token_usage)

**Meilisearch: decision — shared instance**
- ✅ Choice: one shared Meilisearch instance for all tenants (~430 MB total, not per container).
- LibreChat filters search queries on `userId`. Zitadel provides globally unique user IDs — no overlap between tenants possible. Data isolation is guaranteed in practice.
- Each LibreChat container points via `MEILI_HOST` to the same Meilisearch instance. No namespace conflicts for search results (queries always filter on the logged-in user).
- RAM impact: ~200-300 MB idle per LibreChat container (without own Meilisearch). Core-01 is an EX44 (64 GB RAM) — room for 50+ containers on that memory footprint.

---

### AI Routing: LibreChat + LiteLLM + vLLM

**LibreChat + LiteLLM as OpenAI-compatible endpoint**
- ✅ Standard documented pattern. Configuration in `librechat.yaml` under `endpoints.custom`.
- Docs: [librechat.ai/docs/configuration/librechat_yaml/ai_endpoints/litellm](https://www.librechat.ai/docs/configuration/librechat_yaml/ai_endpoints/litellm)

**LiteLLM Complexity Router**
- ✅ Available in LiteLLM OSS from v1.74.9. Not explicitly Enterprise-only. Scoring on 7 dimensions, sub-millisecond, no external calls.
- ⚠️ Relatively new feature, not yet extensively tested in production. Verify OSS status on upgrade.
- Docs: [docs.litellm.ai/docs/proxy/auto_routing](https://docs.litellm.ai/docs/proxy/auto_routing)

**LiteLLM to vLLM: configuration requirements**
- ⚠️ Provider prefix must be `hosted_vllm/` (not `openai/`), otherwise routing errors.
- ⚠️ `drop_params: true` required in litellm_settings — vLLM does not accept all OpenAI parameters.
- Docs: [docs.litellm.ai/docs/providers/vllm](https://docs.litellm.ai/docs/providers/vllm)

**vLLM: gpu-memory-utilization values were incorrect**
- ❌ The values 0.41 + 0.12 were wrong. `--gpu-memory-utilization` is a ceiling on total VRAM per instance. At 0.41 * 80 GB = 32.8 GB ceiling for the 32B instance (while weights are already ~33 GB) virtually no room remains for KV cache — almost certainly OOM or crashing startup.
- Correct approach: split so combined utilization < 1.0. Guideline: 32B instance ~0.55, 8B instance ~0.40, total ~76 GB (weights + KV cache).
- Docs: [docs.vllm.ai/en/stable/configuration/optimization](https://docs.vllm.ai/en/stable/configuration/optimization/)

**vLLM: two instances on one GPU**
- ✅ Supported (vLLM FAQ confirms this explicitly), but is a workaround, not a first-class feature.
- ⚠️ Requires sequential startup: first 32B, then 8B, then Whisper. Parallel startup triggers a memory accounting bug where the second instance sees the VRAM of the first as occupied. [GitHub #10643](https://github.com/vllm-project/vllm/issues/10643)

---

### GPU Resource Management: H100 + MPS + Whisper

**VRAM calculation**
- ⚠️ The 38 GB KV cache is too optimistic. vLLM loads models first in full precision for quantization (peak usage up to 3x model size during startup). CTranslate2 (Whisper) reserves CUDA memory pools above the raw weights. Realistic: ~25-30 GB KV cache after CUDA overhead.

**NVIDIA MPS setup**
- ✅ MPS is the standard approach for GPU sharing between multiple processes.
- ⚠️ Requires two system-level steps before CUDA processes start: GPU compute mode to EXCLUSIVE_PROCESS and start MPS daemon (`nvidia-cuda-mps-control -d`).
- ⚠️ On H100 (Hopper): `CUDA_MPS_ENABLE_PER_CTX_DEVICE_MULTIPROCESSOR_PARTITIONING=1` alongside `CUDA_MPS_ACTIVE_THREAD_PERCENTAGE`, otherwise SM limits may not be enforced per-context.
- ⚠️ vLLM CUDAGraph + MPS can cause instability (illegal memory access). Workaround: `--enforce-eager` on the smaller instance.
- Docs: [docs.nvidia.com/deploy/mps](https://docs.nvidia.com/deploy/mps/index.html)

**faster-whisper on H100**
- ✅ CTranslate2 (the Whisper runtime) supports CUDA GPU inference with `int8_float16` as recommended compute type.
- ⚠️ Requires CUDA 12 + cuDNN 9. Version mismatch is the most common deployment problem.
- ⚠️ Actual VRAM usage: ~6-10 GB (not 4-6 GB) due to CTranslate2 memory pools.

---

### Per-Tenant Routing: Caddy + Wildcard DNS

**"Caddy handles routing automatically" is an oversimplification**
- ⚠️ Caddy does not automatically know about new containers. An explicit approach is needed.

**Recommended approach: Tenant Router (FastAPI dispatcher)**
- ✅ Caddy has a static config with one `*.getklai.com` block. A small FastAPI dispatcher reads the tenant registration (from existing DB) and proxies to the correct container via Docker network hostname.
- Benefit: no Caddy Admin API calls on provisioning, no custom Caddy build for routing, easy to debug.

**Wildcard TLS for *.getklai.com**
- ⚠️ Requires a custom Caddy build (via `xcaddy`) with a DNS provider plugin for ACME DNS-01 challenge. Cloud86 (current DNS provider) has no Caddy plugin.
- ✅ Recommended solution: migrate DNS to **Hetzner DNS** (free, already a customer). Caddy plugin available: `github.com/caddy-dns/hetzner`. Fully European (German), GDPR-compliant, no data outside EU.
- Alternative EU providers with Caddy plugin: INWX (German, `caddy-dns/inwx`), OVH (French, `caddy-dns/ovh`), Bunny DNS (Slovenian, `caddy-dns/bunny`).
- Docs: [caddyserver.com/docs/automatic-https](https://caddyserver.com/docs/automatic-https)

---

### RAG Stack

**LibreChat rag_api + HuggingFace TEI**
- ✅ Confirmed supported. `EMBEDDINGS_PROVIDER=huggingfacetei` is a documented option.
- ⚠️ Requires the non-lite image: `ghcr.io/danny-avila/librechat-rag-api-dev:latest`. The lite image does not support TEI embeddings.
- ⚠️ `EMBEDDINGS_MODEL` must be the TEI service URL, not a model name.
- ⚠️ Known regression (September 2025) in main branch — pin to a tested release. [GitHub #9862](https://github.com/danny-avila/LibreChat/issues/9862)
- Docs: [librechat.ai/docs/configuration/rag_api](https://www.librechat.ai/docs/configuration/rag_api)

**BGE-M3 VRAM via TEI**
- ⚠️ 2 GB is too optimistic. Production with batching: 4-10 GB. Check TEI version for sparse/multi-vector support — that is precisely the added value of BGE-M3 over smaller models. [TEI GitHub #141](https://github.com/huggingface/text-embeddings-inference/issues/141)

**Qdrant v1.16 (Track B)**
- ✅ Tiered multitenancy is native and production-grade. Small tenants share a fallback shard, large tenants are automatically promoted to a dedicated shard.
- ⚠️ Custom sharding must be configured at collection creation. Adding it later requires data migration.
- Docs: [qdrant.tech/documentation/guides/multitenancy](https://qdrant.tech/documentation/guides/multitenancy/)

**Docling-serve**
- ✅ Official FastAPI/Uvicorn wrapper (`docling-serve`). Supports sync/async, API key auth, Docker images for CPU/CUDA. Production-ready.
- Ref: [github.com/docling-project](https://github.com/docling-project)

**Haystack 2.x or LlamaIndex + Qdrant**
- ✅ Both fully compatible with Qdrant. Haystack via `qdrant-haystack` package, LlamaIndex via `llama-index-vector-stores-qdrant`. Both documented.
- ⚠️ Haystack: `use_sparse_embeddings=True` must be explicitly set for BGE-M3 hybrid retrieval. Default is dense-only.

---

### Monitoring: Grafana Alloy + VictoriaLogs/Metrics

**Alloy + VictoriaLogs**
- ✅ Compatible. VictoriaLogs has a Loki-compatible push endpoint, Alloy's `loki.write` component works with it.
- Docs: [docs.victoriametrics.com/victoriametrics/data-ingestion/alloy](https://docs.victoriametrics.com/victoriametrics/data-ingestion/alloy/)

**Alloy + VictoriaMetrics**
- ✅ Compatible via standard Prometheus remote write protocol.

**Grafana datasource: dedicated plugins required**
- ❌ VictoriaLogs: generic Loki datasource does **not** work — LogsQL (VictoriaLogs) and LogQL (Loki) are incompatible. Plugin required: `victoriametrics-logs-datasource`.
- ⚠️ VictoriaMetrics: generic Prometheus datasource mostly works. Plugin (`victoriametrics-metrics-datasource`) adds MetricsQL autocomplete and WITH templates — useful but not a hard requirement.
- Install via env var: `GF_INSTALL_PLUGINS=victoriametrics-logs-datasource,victoriametrics-metrics-datasource`
- Docs: [grafana.com/grafana/plugins/victoriametrics-logs-datasource](https://grafana.com/grafana/plugins/victoriametrics-logs-datasource/)

**LiteLLM: per-tenant metrics via Prometheus**
- ✅ LiteLLM exposes `/metrics` with spend, tokens and latency labeled per team, user, API key and model. Configurable via `prometheus_metrics_config`.
- This provides a direct billing basis per tenant — no custom ETL needed for token usage.
- Pre-built dashboard available: [Grafana Dashboard #24055](https://grafana.com/grafana/dashboards/24055-litellm/)
- Docs: [docs.litellm.ai/docs/proxy/prometheus](https://docs.litellm.ai/docs/proxy/prometheus)

**vLLM: per-instance metrics via Prometheus**
- ✅ vLLM exposes `/metrics` with KV cache occupancy, queue depth, token throughput and latency.
- Pre-built dashboard available: [Grafana Dashboard #23991](https://grafana.com/grafana/dashboards/23991-vllm/)
- Docs: [docs.vllm.ai/en/latest/design/metrics](https://docs.vllm.ai/en/latest/design/metrics/)

**Deployment pattern: Alloy per server + central monitor-01**
- ✅ This is the standard documented pattern for small-to-medium setups.

---

### Summary: critical actions

*Last verified: 2026-03-22*

| Status | Component | Action / Decision |
|---|---|---|
| ✅ Done | LibreChat database | MongoDB as shared server, separate database per tenant via `MONGO_URI`. Silo model. |
| ✅ Done | vLLM gpu-memory-utilization | Corrected: ~0.55 (32B) + ~0.40 (8B). Original (0.41 + 0.12) was incorrect. |
| ✅ Done | Caddy wildcard TLS | `caddy-hetzner:latest` deployed. xcaddy + `github.com/caddy-dns/hetzner`. DNS on Hetzner DNS. |
| ✅ Done | Meilisearch | Shared instance. Isolation via Zitadel globally unique userId filtering. |
| ✅ Done | Grafana VictoriaLogs plugin | Required (`victoriametrics-logs-datasource`). LogsQL != LogQL, generic Loki does not work. |
| ✅ Done | LibreChat provisioning | portal-api provisioning service live (Phase 1 complete). Template logic: `provisioning.py`. |
| ✅ Done | LiteLLM `drop_params` | `drop_params: true` in `litellm/config.yaml`. |
| ✅ Done | Qdrant | Deployed on core-01. `klai_knowledge` + `klai_focus` collections. Replaces pgvector for vector search. |
| ✅ Done | knowledge-ingest | Custom ingest service deployed. Gitea webhook, document upload, crawl, personal items endpoints. |
| ✅ Done | retrieval-api | Hybrid RRF retrieval (dense + question + sparse) deployed on core-01. `POST /retrieve`. |
| ✅ Done | bge-m3-sparse | FlagEmbedding sparse sidecar deployed. BGE-M3 dense+sparse in production. |
| ✅ Done | klai-knowledge-mcp | MCP write server deployed. Personal + org KB saves; indexed in Qdrant immediately. |
| ✅ Done | LiteLLM knowledge hook | `KlaiKnowledgeHook` live with feature gate, user_id scoping, conversation history, gap detection. |
| ⚠️ Partial | LibreChat OIDC | `OPENID_USERNAME_CLAIM=preferred_username` + `OPENID_REUSE_TOKENS=false` in provisioning.py ✅. Custom logout redirect to Zitadel end-session still missing. |
| ⚠️ Open | Cleopatra/Voys-UI | Publicly available? React + Tailwind v4 compatible? Verify internally before portal build. |
| 🔜 Phase 3+ | LiteLLM to vLLM prefix | `hosted_vllm/` prefix in LiteLLM config — applicable when ai-01 GPU server is live. |
| 🔜 Phase 3+ | MPS system setup | cloud-init or systemd unit for: EXCLUSIVE_PROCESS mode + MPS daemon + Hopper env vars, before Docker starts. |
| N/A | rag_api image | Not used — custom knowledge-ingest + retrieval-api built instead of LibreChat rag_api. |
