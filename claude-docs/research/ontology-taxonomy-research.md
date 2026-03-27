# Ontology & Taxonomy Research

> Research date: 2026-03-23
> Context: Klai Knowledge platform — informing ontology + hybrid retrieval architecture
> Triggered by: comparison with ThetaOS personal PKM system at PKM Summit 2026-03-20–23
> Status: Active reference — informs entity registry design and hybrid retrieval architecture

---

## Contents

| § | Section |
|---|---|
| 1 | [What the terms mean](#1-what-the-terms-mean) |
| 2 | [Ontology design patterns that matter in practice](#2-ontology-design-patterns-that-matter-in-practice) |
| 3 | [Hybrid retrieval: when SQL beats vectors (and vice versa)](#3-hybrid-retrieval-when-sql-beats-vectors-and-vice-versa) |
| 4 | [Wikilinks and universal identifiers](#4-wikilinks-and-universal-identifiers) |
| 5 | [Entity-enhanced RAG: production patterns](#5-entity-enhanced-rag-production-patterns) |
| 6 | [Common entity types in B2B knowledge systems](#6-common-entity-types-in-b2b-knowledge-systems) |
| 7 | [Common relationship types](#7-common-relationship-types) |
| 8 | [Taxonomy and classification systems](#8-taxonomy-and-classification-systems) |
| 9 | [The starter ontology for Klai Knowledge](#9-the-starter-ontology-for-klai-knowledge) |
| 10 | [Multi-tenant database schema: fixed core + dynamic JSONB](#10-multi-tenant-database-schema-fixed-core--dynamic-jsonb) |
| 11 | [Architecture recommendation](#11-architecture-recommendation) |
| 12 | [Lessons from real-world failures](#12-lessons-from-real-world-failures) |

---

## 1. What the terms mean

**Taxonomy** is pure hierarchy: parent-child classification. "A product is a type of artifact. A laptop is a type of product." No lateral relationships, no properties beyond membership. Every system has a taxonomy whether they call it that or not.

**Ontology** is the formal schema layer: defines entity types, their properties, and the legal relationship types between them. "A Person has a name (string) and may have an `employedBy` relationship to an Organization." It answers "what can exist and how can things relate." In practice: most teams implement this as a PostgreSQL schema + application-level validation, not as OWL/RDF files.

**Knowledge Graph** is the ontology instantiated with real data: "Alice (Person) employedBy Klai (Organization), founded 2021." The formula: `ontology + instance data = knowledge graph`.

The three are not competing alternatives — they are sequential layers. The question is which layers you build explicitly vs. implicitly.

### Standards that are actually used in production

| Standard | What it is | Production use |
|---|---|---|
| **OWL 2** | Full description logic | Biomedical, legal, enterprise master data — overkill for most B2B |
| **SKOS** | Controlled vocabularies, taxonomies | Libraries, governments (EU ESCO), enterprise taxonomies |
| **PROV-O** | Provenance ontology | Data governance, FAIR data — directly maps to Klai's `provenance_type` |
| **W3C ORG** | Organizational structure | W3C Recommendation; defines Organization, OrganizationalUnit, Role, Post |
| **schema.org** | Shared web vocabulary | SEO, structured data — not for deep reasoning |
| **FOAF** | Person + social graph | Online identity; basis for person-org relationship modeling |

**Practical takeaway for Klai:** Use SKOS concepts for concept hierarchies, PROV-O concepts for provenance (already aligned with `provenance_type`), PostgreSQL for the knowledge graph instance layer. Full OWL/RDF is engineering overhead without practical benefit for a closed-domain knowledge platform.

---

## 2. Ontology design patterns that matter in practice

### Pattern 1: The Entity Registry

A central table of canonical entities — each with a stable identifier, canonical name, type, and aliases. Everything else references this registry. This is what makes ThetaOS's wikilink approach work: the filesystem functions as a canonical identifier store.

```sql
CREATE TABLE knowledge.entities (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    type            TEXT NOT NULL,  -- person, organization, product_area, feature, concept, ...
    canonical_name  TEXT NOT NULL,
    wikilink        TEXT NOT NULL,  -- [[type:name]] — unique per tenant
    aliases         TEXT[],
    properties      JSONB DEFAULT '{}',
    created_at      BIGINT NOT NULL,
    UNIQUE(tenant_id, wikilink)
);
```

### Pattern 2: Assertion-Evidence separation

Separate *what is claimed* from *the evidence for the claim*. This maps directly to Klai's `assertion_mode` (factual/belief/hypothesis) and `provenance_type`. PROV-O formalizes this as: Activity → generated → Entity, wasAttributedTo → Agent. Already conceptually implemented in Klai — just needs to be queryable via entity linkage.

### Pattern 3: Temporal Validity

Every relationship has two timestamps: when it was true in the world (`valid_from`) and when it ended (`valid_until`). Zep/Graphiti implement this explicitly — conflicting facts invalidate old edges rather than deleting them. This answers "what did we believe about this process on date X?"

### Pattern 4: Roles are relationships, not types (the most-cited anti-pattern)

"Bob is a teacher" should be `Bob holds_role Teacher at SchoolX`, not `Bob isA Teacher`. When Bob retires, the entity survives but the role relationship ends. Modeling roles as entity types (or worse, as entity subtypes) is the single most cited ontology design failure in enterprise KM literature.

---

## 3. Hybrid retrieval: when SQL beats vectors (and vice versa)

The 2026 consensus: "vectors for breadth, graphs/SQL for depth." Running both in parallel and merging with RRF is better than routing to one exclusively.

### When SQL/graph decisively wins

| Query characteristic | Why |
|---|---|
| Named entity lookup: "everything about product X" | Deterministic. SQL WHERE clause, no semantic drift. |
| Relationship traversal: "who reports to the CEO?" | Multi-hop traversal — vectors cannot represent the path. |
| Temporal filters: "what was true as of Q3 2024?" | Bi-temporal SQL. Embeddings carry no temporal information. |
| Audit / compliance queries | SQL is deterministic, debuggable, explainable. |
| Schema-heavy enterprise queries | FalkorDB benchmark: 90%+ accuracy with graph vs. 0% vector RAG for strategy/KPI queries. |

### When vectors decisively win

| Query characteristic | Why |
|---|---|
| Semantic similarity: "content about cloud cost reduction" | Captures synonyms, paraphrases, conceptual similarity. |
| Exploratory search — user doesn't know the entity name | User can't look up what they don't know to look for. |
| Cross-lingual retrieval | BGE-M3 shared embedding space — SQL knows nothing about language. |
| Novel content not yet in the ontology | Vectors work on raw text; SQL requires prior schema population. |
| Long-tail concepts without dedicated entity records | Not everything deserves an entity; vectors handle the long tail. |

### The "lookup first, vector second" pattern

The concrete routing logic from production systems (Zep, Weaviate+Neo4j, Cedars-Sinai medical KG):

```
1. Parse query for entity mentions (GLiNER NER)
2. If entity mentions found AND entity exists in registry:
   → SQL lookup: entity profile + related artifacts (high precision)
   → Use entity context to expand/filter the vector query
3. Always: Qdrant vector search (filtered by entity_id if known)
4. Merge via Reciprocal Rank Fusion (RRF)
5. Construct LLM context: entity profiles first, then retrieved chunks
```

Zep achieves P95 latency of 300ms with this pattern by running entity extraction at ingestion time, not at query time.

### Microsoft GraphRAG: the actual overhead

**Standard GraphRAG**: LLM-powered entity extraction + Leiden community detection + hierarchical community summaries. Cost: "several to dozens of times more tokens than the original text." Benefit: 3.4x accuracy on relationship queries. Justified only for corpus-wide synthesis.

**LazyGraphRAG** (Microsoft Research, late 2024): defers all LLM use to query time, NLP noun phrase extraction instead. Indexing cost = 0.1% of full GraphRAG. Query cost = 4%. Quality: "significantly outperforms competing methods" at this cost level. "700x lower query cost" at matched quality. **This is the pragmatic on-ramp.**

---

## 4. Wikilinks and universal identifiers

### The wikilink pattern

ThetaOS's `[[👥 Gonnie Tutelaers]]` functions simultaneously as:
1. **Human-readable label** in markdown text
2. **Filesystem identifier** (Obsidian filename)
3. **SQL WHERE clause** (`WHERE wikilink = '[[👥 Gonnie Tutelaers]]'`)

The power: one naming decision propagates everywhere. The fragility: rename the entity and all references break unless redirects are maintained.

### Wikilinks vs. UUIDs: the tradeoff

| Criterion | UUID | Wikilink ([[type:name]]) |
|---|---|---|
| Stability on rename | Perfect | Breaks without redirect table |
| Human readability | Zero | High — immediately meaningful |
| Authoring friction | High — must know the UUID | Low — just type the name |
| AI anchor quality | Good but opaque | Excellent — LLM understands the label semantically |
| Cross-system interoperability | High | Scoped to your namespace |

**Conclusion for Klai:** Use wikilinks as the human-facing identifier, UUIDs as the stable machine identifier, entity registry maps both. Structure: `[[type:name]]` — e.g. `[[org:Voys]]`, `[[product_area:Billing]]`, `[[person:Gonnie Tutelaers]]`. Add a redirect table for renames.

### Automatic wikilink resolution at ingest

Pipeline for linking incoming documents to the entity registry:
1. **GLiNER NER**: identify entity spans in document text
2. **Candidate generation**: fuzzy-match spans against `entities.aliases[]` and `entities.canonical_name`
3. **Disambiguation**: use surrounding context + entity type if multiple candidates
4. **Link injection**: store as `entity_mention` record referencing `entity.id`

**GLiNER** (NAACL 2024): zero-shot NER for custom entity types, runs on CPU, outperforms ChatGPT and fine-tuned LLMs on NER benchmarks. The right tool for this.

---

## 5. Entity-enhanced RAG: production patterns

### The spectrum (pick your level by ROI)

**Level 0 — Metadata filtering** (1-2 days): Tag documents with entity IDs at ingest. Filter Qdrant search by entity ID. "Show me all documents about product X" = vector search WHERE entity_id = X. Immediate precision gain.

**Level 1 — Entity context injection** (1 week): When a query mentions a known entity, prepend the entity's structured record (name, type, relations) to LLM context alongside retrieved chunks. Significant reduction in entity-related hallucination.

**Level 2 — Relationship traversal** (2-4 weeks): For multi-hop queries, traverse the entity graph before vector search. "Find all knowledge related to our top 3 partners" = look up partners → traverse relationships → gather associated documents → rank by vector similarity.

**Level 3 — Full GraphRAG community summaries** (weeks, high cost): LLM-powered entity extraction, Leiden clustering, pre-computed community summaries. Enables corpus-wide synthesis. Use LazyGraphRAG variant.

### Zep/Graphiti: the reference production implementation

Zep (arxiv:2501.13956, January 2025):
- **Three-tier graph**: Episodes (raw text) → Semantic Entities (extracted, deduplicated) → Communities
- **Entity extraction at ingestion time** — critical design choice; shifts cost to write path, keeps query latency low
- **Deduplication**: 1024-d embedding + BM25 against existing entities; LLM for ambiguous cases
- **Temporal tracking**: bi-temporal; conflicting facts invalidate prior edges, not overwrite
- **Performance**: P95 latency 300ms; 63.8–71.2% accuracy on LongMemEval vs. 55.4–60.2% baseline

### What Salesforce Knowledge does right

Salesforce Knowledge associates every article with:
- `Data Categories` — a mandatory two-level hierarchical taxonomy (`product_area` → `feature`)
- `Article Type` — structural template (FAQ, How-To, Troubleshooting)
- `Case` links — every resolved support case can link to the article that resolved it

This means "which articles resolved the most cases?" is a direct SQL query — no ML required. Klai's gap detection pipeline is the equivalent: transcript → gap → resolving article. The entity graph makes this traversable.

---

## 6. Common entity types in B2B knowledge systems

### What the major standards define

**Schema.org** most relevant B2B types:
`Organization`, `Person`, `Product`, `Service`, `Event`, `CreativeWork`, `SoftwareApplication`

**W3C ORG Ontology** (W3C Recommendation):
`Organization`, `FormalOrganization`, `OrganizationalUnit`, `Membership`, `Role`, `Post`, `Site`, `ChangeEvent`

**Salesforce CRM** (de facto B2B data model):
`Account` (organization), `Contact` (person at an account), `Case` (support request), `Product`, `Knowledge Article`

**Zendesk Guide** article types:
`FAQ`, `How-To`, `Troubleshooting`, `Reference`, `Glossary`, `Product Description`, `Release Note`

### Universal entity types — appear in virtually every B2B knowledge system

1. **Person** — humans who work at, buy from, or interact with organizations
2. **Organization** — legal entities, teams, departments, customer companies
3. **Product / Service** — what the organization makes or sells
4. **Feature / Capability** — a bounded part of a product
5. **Process / Workflow** — a repeatable sequence of steps
6. **Concept / Term** — a definition, category, or abstract idea
7. **Event / Incident** — a time-bounded occurrence (release, outage, meeting)
8. **Project / Initiative** — a time-bounded effort with a goal
9. **Role / Position** — an abstract function a person fills
10. **Document / Artifact** — handled by `knowledge_artifacts` in Klai

---

## 7. Common relationship types

### Person → Organization
- `employed_by` (W3C ORG: `memberOf`)
- `founded_by` (schema.org: `founder`)
- `head_of` (W3C ORG: `headOf`)
- `reports_to`

### Organization → Organization
- `subsidiary_of` / `parent_of` (schema.org: `parentOrganization`)
- `partner_of`
- `supplier_of` / `customer_of`
- `competitor_of`

### Person → Person
- `reports_to` / `manager_of`
- `colleague_of` (FOAF: `knows`)
- `mentor_of`

### Knowledge → Entity
- `about` (schema.org: `about`) — primary subject
- `mentions` (schema.org: `mentions`) — incidental reference
- `created_by` / `authored_by`
- `verified_by` / `reviewed_by`
- `supersedes` / `is_superseded_by`
- `triggered_by` — article created in response to an event/incident

### Process / Feature → Product
- `describes` — documentation of a product capability
- `part_of` — compositional hierarchy (feature → product area)
- `depends_on` — technical dependency

---

## 8. Taxonomy and classification systems

### Universal facets — appear across all B2B knowledge systems

| Facet | Type | Example values |
|---|---|---|
| **Content type** | Controlled vocab | `how_to`, `faq`, `troubleshooting`, `reference`, `policy`, `release_note`, `glossary_entry`, `runbook` |
| **Product area** | FK to entity registry | links to entity of type `product_area` |
| **Audience** | Controlled vocab | `customer`, `partner`, `internal`, `developer`, `admin` |
| **Lifecycle status** | Controlled vocab | `draft`, `in_review`, `published`, `archived`, `deprecated` |
| **Confidence / Verification** | Ordinal | `verified`, `unverified`, `needs_review` |
| **Sensitivity** | Access control | `public`, `internal`, `confidential` |
| **Language** | ISO 639-1 | `nl`, `en`, `de` |
| **Tags** | text[] | free + controlled vocabulary |

These complement the existing Klai fields `provenance_type`, `assertion_mode`, and `synthesis_depth` — they are orthogonal, not overlapping.

### What the standards add

- **ITIL CTI** (Category/Type/Item): the 3-tier incident/problem classification used by ServiceNow and Jira Service Management. `product_area` → `content_type` → specific article maps to this pattern.
- **Dublin Core `dcterms:audience`**: directly maps to the `audience` facet.
- **Salesforce Data Categories**: mandatory two-level taxonomy on every Knowledge Article — validated production pattern for the `product_area` → `feature` hierarchy.

### Taxonomy governance

Three required governance artefacts (Enterprise Knowledge taxonomy research):
1. **A vocabulary steward** — a human or team responsible for approving new terms
2. **A version history** — audit log on the predicate registry and entity type registry
3. **An evolution process** — new types proposed, piloted in one tenant's data, then promoted to system defaults

Without governance: tag entropy destroys taxonomy value within 12-18 months in active enterprise deployments.

---

## 9. The starter ontology for Klai Knowledge

### Entity types

| Type | Description | Example instances |
|---|---|---|
| `person` | A human being | Mark Vos (support agent), Fatima Al-Rashid (customer) |
| `organization` | A company, institution, or legal entity | Voys, Klai, Acme Corp |
| `team` | An organizational sub-unit | Support Team NL, Product Engineering |
| `role` | An abstract position (not the person, the role) | Customer Success Manager, L2 Support Engineer |
| `product_area` | A bounded functional domain of the product | Billing, Authentication, Dial Plans, Integrations |
| `feature` | A specific, named capability | VoIP Call Recording, SSO via SAML, Number Porting |
| `concept` | A defined term or abstract domain idea | SLA, MTTR, Portability Window |
| `process` | A repeatable multi-step procedure | Onboarding Flow, Escalation Procedure |
| `event` | A time-bounded occurrence | Product Launch v2.3, Incident 2024-11-12 |
| `project` | A time-bounded initiative with a goal | EU Data Residency Migration, GDPR Audit Q4 |

**Current Klai types:** `product_area`, `feature`, `concept`, `person` — add: `organization`, `team`, `role`, `process`, `event`, `project`.

### Relationship types

| Predicate | Subject | Object | Notes |
|---|---|---|---|
| `employed_by` | person | organization | Current employment |
| `member_of` | person | team | Team membership |
| `holds_role` | person | role | Active role assignment — with `valid_until` |
| `part_of` | team | organization | Department containment |
| `part_of` | feature | product_area | Feature-to-area grouping |
| `sub_process_of` | process | process | Process hierarchy |
| `reports_to` | person | person | Direct management |
| `partner_of` | organization | organization | Commercial partnership |
| `customer_of` | organization | organization | Customer relationship direction |
| `successor_of` | concept | concept | Definition supersession |
| `about` | knowledge_artifact | entity (any) | Primary subject |
| `mentions` | knowledge_artifact | entity (any) | Incidental reference |
| `created_by` | knowledge_artifact | person | Authorship |
| `verified_by` | knowledge_artifact | person | Verification provenance |
| `supersedes` | knowledge_artifact | knowledge_artifact | Content lineage |
| `triggered_by` | knowledge_artifact | event | Event-sourced content |
| `related_to` | entity | entity | Generic soft linkage — last resort |

### SQL schema additions

```sql
-- Extend existing knowledge.entities
ALTER TABLE knowledge.entities
  ADD COLUMN wikilink    text NOT NULL,        -- [[type:name]], unique per tenant
  ADD COLUMN aliases     text[] DEFAULT '{}',  -- alternate names for disambiguation
  ADD COLUMN properties  jsonb DEFAULT '{}',   -- type-specific extensible attributes
  ADD UNIQUE(tenant_id, wikilink);

-- Relationship registry
CREATE TABLE knowledge.entity_relationships (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL,
  subject_id   uuid NOT NULL REFERENCES knowledge.entities(id),
  predicate    text NOT NULL,       -- 'employed_by', 'part_of', 'about', ...
  object_id    uuid NOT NULL REFERENCES knowledge.entities(id),
  source_id    uuid,                -- which artifact asserts this relationship
  confidence   float,
  valid_from   bigint,              -- Unix epoch
  valid_until  bigint,              -- Unix epoch; null = still valid
  created_at   bigint NOT NULL
);

-- Entity mentions in artifacts (from NER extraction at ingest)
CREATE TABLE knowledge.entity_mentions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id       uuid NOT NULL REFERENCES knowledge.entities(id),
  artifact_id     uuid NOT NULL REFERENCES knowledge.artifacts(id),
  link_type       text NOT NULL,    -- 'about', 'mentions'
  span_start      int,
  span_end        int,
  context_snippet text,
  confidence      float,
  tenant_id       uuid NOT NULL
);

-- Taxonomy classification on knowledge artifacts
ALTER TABLE knowledge.artifacts
  ADD COLUMN content_type      text,           -- enum at app layer
  ADD COLUMN product_area_id   uuid REFERENCES knowledge.entities(id),
  ADD COLUMN audience          text,
  ADD COLUMN lifecycle_status  text DEFAULT 'draft',
  ADD COLUMN confidence_level  text DEFAULT 'unverified',
  ADD COLUMN sensitivity       text DEFAULT 'internal',
  ADD COLUMN language          char(2) DEFAULT 'nl',
  ADD COLUMN tags              text[];

-- Predicate registry (controls valid relationship types per tenant)
CREATE TABLE knowledge.relationship_predicates (
  tenant_id     uuid NOT NULL,
  predicate     text NOT NULL,
  label_nl      text,
  label_en      text,
  subject_types text[],    -- which entity types may be subjects
  object_types  text[],    -- which entity types may be objects
  is_system     boolean DEFAULT false,   -- system predicates = platform defaults
  PRIMARY KEY (tenant_id, predicate)
);

-- Redirect table for renamed entities
CREATE TABLE knowledge.entity_redirects (
  old_wikilink   text NOT NULL,
  new_entity_id  uuid NOT NULL REFERENCES knowledge.entities(id),
  tenant_id      uuid NOT NULL,
  created_at     bigint NOT NULL,
  PRIMARY KEY (tenant_id, old_wikilink)
);
```

### Multi-tenant extension design

- `is_system = true` predicates in `relationship_predicates` are platform defaults, copied to each new tenant at provisioning
- Tenants can add custom predicates (`is_system = false`) without affecting other tenants
- The `properties jsonb` column on entities absorbs tenant-specific attributes without schema migrations
- All tables partitioned by `tenant_id`

---

## 10. Multi-tenant database schema: fixed core + dynamic JSONB

Multi-tenant SaaS creates a fundamental tension: tenants want custom fields, but `ALTER TABLE ADD COLUMN` per tenant is unworkable at scale. Full JSONB solves the migration problem but loses the indexing and type-safety of relational columns. The solution is a hybrid: a fixed core for universal attributes, JSONB for everything dynamic, and PostgreSQL's indexing primitives to make the dynamic part fast.

### The fixed core

Every entity has the same universal attributes — these get real columns with real indexes:

```sql
CREATE TABLE knowledge.entities (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL,
  type           text NOT NULL,        -- person, org, product_area, feature, concept ...
  canonical_name text NOT NULL,
  wikilink       text NOT NULL,        -- [[type:name]], unique per tenant
  aliases        text[] DEFAULT '{}',  -- GIN-indexable
  is_active      boolean DEFAULT true,
  created_at     bigint NOT NULL,
  updated_at     bigint NOT NULL,
  UNIQUE(tenant_id, wikilink)
);

CREATE INDEX ON knowledge.entities (tenant_id, type);
CREATE INDEX ON knowledge.entities (tenant_id, is_active);
CREATE INDEX ON knowledge.entities USING GIN (aliases);
```

### The dynamic part: JSONB with a GIN index

All type-specific and tenant-specific attributes go into `props`. A GIN index makes arbitrary JSONB field queries fast without any schema migration:

```sql
ALTER TABLE knowledge.entities ADD COLUMN props jsonb DEFAULT '{}';

CREATE INDEX ON knowledge.entities USING GIN (props);
```

Adding a new field to any entity requires no migration — just write to `props`:

```sql
-- No migration needed
UPDATE knowledge.entities
SET props = props || '{"kvk_nummer": "12345678"}'
WHERE id = '...';

-- Immediately queryable via GIN index
SELECT * FROM knowledge.entities
WHERE tenant_id = $1 AND props->>'kvk_nummer' = '12345678';
```

### Full-text search: generated column, zero maintenance

PostgreSQL's `GENERATED ALWAYS AS ... STORED` gives a real indexed column that PostgreSQL maintains automatically — no triggers, no application-side updates:

```sql
ALTER TABLE knowledge.entities
  ADD COLUMN search_vector tsvector
  GENERATED ALWAYS AS (
    to_tsvector('simple',
      canonical_name || ' ' ||
      coalesce(array_to_string(aliases, ' '), '') || ' ' ||
      coalesce(props->>'description', '')
    )
  ) STORED;

CREATE INDEX ON knowledge.entities USING GIN (search_vector);
```

### Partial functional indexes: per-tenant fast paths, zero migration

The most underused PostgreSQL feature for multi-tenant schemas. When a specific tenant queries a specific JSONB field heavily, create a targeted index for them without touching other tenants:

```sql
-- Voys queries phone numbers frequently on person entities
CREATE INDEX CONCURRENTLY ON knowledge.entities
  ((props->>'phone'))
  WHERE tenant_id = 'voys-uuid' AND type = 'person';

-- Klai needs fast KvK number lookup
CREATE INDEX CONCURRENTLY ON knowledge.entities
  ((props->>'kvk_nummer'))
  WHERE tenant_id = 'klai-uuid' AND type = 'organization';
```

Each index is invisible to other tenants. `CONCURRENTLY` means no table lock. This is operationally equivalent to adding a column to a tenant's "virtual schema" without any actual schema change.

### Entity type definitions: schema without migration

A `entity_type_definitions` table documents the expected shape of each entity type as a JSON Schema. Validation happens at the application layer, not the database layer — this means schema evolution (adding a field, changing a type) requires no migration:

```sql
CREATE TABLE knowledge.entity_type_definitions (
  tenant_id    uuid NOT NULL,
  entity_type  text NOT NULL,
  is_system    boolean DEFAULT false,  -- platform defaults vs. tenant-custom
  props_schema jsonb NOT NULL,         -- JSON Schema for app-level validation
  PRIMARY KEY (tenant_id, entity_type)
);
```

`is_system = true` types are the platform defaults. At tenant provisioning, they are copied to the new tenant. A tenant can extend a system type with additional fields in their own definition without affecting any other tenant. The same pattern applies to `relationship_predicates`.

### Summary: what this pattern delivers

| Requirement | How it's met |
|---|---|
| New tenant onboarding | Zero migrations — type definitions copied at provisioning |
| Tenant wants a custom field | Write to `props`, optionally create a partial index |
| Query on a universal field | Real column + real index — full PostgreSQL speed |
| Query on a dynamic field | GIN index on JSONB, or tenant-specific partial functional index |
| Full-text entity search | Generated `tsvector` column — always current, zero overhead |
| Schema documentation | `entity_type_definitions` table — readable, versionable, diffable |
| Tenant isolation | `tenant_id` on every row; row-level security if needed |

This is the same pattern used by Notion, Linear, and HubSpot internally: fixed core for universal attributes, JSONB for the rest, targeted indexing so you never pay migration cost for a new field.

---

## 11. Architecture recommendation

### The architecture in one picture

```
WRITE PATH:
Document arrives
  → GLiNER NER extraction (entity type list from registry)
  → For each entity span: BM25/embedding lookup in entity registry
      → match found: create entity_mention record
      → no match: create entity candidate (pending review)
  → Chunk + embed (BGE-M3, existing TEI)
  → Qdrant upsert with entity_id payload filters
  → PostgreSQL: artifacts + entity_mentions + embedding_queue

READ PATH:
Query arrives
  → GLiNER entity detection
  → SQL entity lookup (exact + alias match)
  → IF entity found:
      entity profile + related artifacts via entity_mentions  (high precision)
  → ALWAYS:
      Qdrant vector search, filtered by entity_id if known  (high recall)
  → RRF merge
  → LLM context: entity profiles first, then retrieved chunks
  → Response
```

### Build order by ROI

| Phase | What | Effort | Gain |
|---|---|---|---|
| 1 | `wikilink` column + `entity_mentions` table | 1-2 days | Entity lookup works |
| 2 | `entity_relationships` + 10 base predicates | 1 week | Relationship traversal works |
| 3 | GLiNER extraction at ingest | 2-3 weeks | Auto-population of entity mentions |
| 4 | "Lookup first, vector second" in `/knowledge/v1/retrieve` | 1-2 weeks | Hybrid retrieval quality boost |
| 5 | Taxonomy columns on `knowledge_artifacts` | 1-2 days | Structured navigation + filtering |
| 6 | LazyGraphRAG community summaries | Conditional on Phase 1-4 success | Corpus-wide synthesis queries |

### The ThetaOS comparison, honestly

ThetaOS "339 tables, no vectors" works because it is a single-user system with a rich pre-curated ontology built over years. Every entity is already in the registry; disambiguation is trivial; queries are deterministic.

For Klai Knowledge (B2B multi-tenant):
- Novel content arrives constantly → vectors needed for content not yet extracted into the ontology
- Multiple organizations → automatic entity extraction required, not human curation
- Multiple users → disambiguation must be automated

The ThetaOS insight is correct: **invest heavily in the librarian layer**. For B2B, the librarian must be built automatically — and that requires GLiNER + the entity registry. The end result is the same: deterministic lookup for known entities, semantic search for the rest.

---

## 12. Lessons from real-world failures

**1. Starting exhaustive.** Enterprise ontology projects most often fail by attempting completeness before utility. Start with 5-8 entity types, validate on real content, then expand. Never design for a hypothetical future corpus.

**2. Roles as types.** Modeling `Teacher` or `SupportEngineer` as entity subtypes makes every role change a data migration. Model roles as relationships with `valid_until`.

**3. Part-of confused with subclass-of.** A `Feature` is `part_of` a `ProductArea`, not a subtype of it. Mixing compositional and taxonomic relationships breaks navigation, search, and reasoning.

**4. OWL/SPARQL in operational systems.** Multiple enterprise projects migrated from RDF triple stores to SQL-backed graph representations. PostgreSQL with `entity_relationships` rows is operationally superior: standard SQL, native joins to `knowledge_artifacts`, evolves via migrations.

**5. Taxonomy designed around content production, not user discovery.** Taxonomies that replicate the org chart ("HR > Policies > Maternity Leave") perform poorly for search. `product_area` + `content_type` + `audience` as required facets creates a user-task-oriented navigation spine.

**6. Ignoring temporal validity.** A feature is deprecated. A policy expires. A contact leaves. Without `valid_from`/`valid_until` on relationships and `lifecycle_status` on artifacts, LLMs retrieve stale facts with equal confidence to current ones.

**7. Vectors-only for entity lookup.** If you know an entity exists, use SQL. Vectors are for when you don't know what you're looking for. Using Qdrant to look up "the Billing product area" is slower, fuzzier, and more expensive than `WHERE wikilink = '[[product_area:Billing]]'`.

---

## Sources

- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory (arxiv:2501.13956)](https://arxiv.org/abs/2501.13956)
- [Graphiti: Knowledge Graph Memory for an Agentic World (Neo4j)](https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/)
- [LazyGraphRAG: Setting a New Standard for Quality and Cost (Microsoft Research)](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)
- [GraphRAG vs Vector RAG: Accuracy Benchmark Insights (FalkorDB)](https://www.falkordb.com/blog/graphrag-accuracy-diffbot-falkordb/)
- [HybridRAG: Why Combine Vector Embeddings with Knowledge Graphs? (Memgraph)](https://memgraph.com/blog/why-hybridrag)
- [GLiNER: Generalist Model for Named Entity Recognition (NAACL 2024)](https://arxiv.org/abs/2311.08526)
- [The Organization Ontology - W3C TR](https://www.w3.org/TR/vocab-org/)
- [Organization - Schema.org Type](https://schema.org/Organization)
- [FOAF Vocabulary Specification](https://xmlns.com/foaf/spec/)
- [Wikidata:WikiProject Companies/Properties](https://www.wikidata.org/wiki/Wikidata:WikiProject_Companies/Properties)
- [Salesforce Data Model](https://developer.salesforce.com/docs/atlas.en-us.object_reference.meta/object_reference/data_model.htm)
- [DCMI Metadata Terms](https://www.dublincore.org/specifications/dublin-core/dcmi-terms/)
- [PROV-O: The PROV Ontology (W3C)](https://www.w3.org/TR/prov-o/)
- [RAG vs. GraphRAG: A Systematic Evaluation (arxiv:2502.11371)](https://arxiv.org/html/2502.11371v1)
- [Ontology Design Best Practices Part II - Enterprise Knowledge](https://enterprise-knowledge.com/ontology-design-best-practices-part-ii/)
- [Taxonomy Governance Best Practices - Enterprise Knowledge](https://enterprise-knowledge.com/taxonomy-governance-best-practices/)
- [Knowledge Base Taxonomy: 10 Principles That Work](https://www.matrixflows.com/blog/knowledge-base-taxonomy-best-practices)
- [TOVE Project - Wikipedia](https://en.wikipedia.org/wiki/TOVE_Project)
- [Entity Resolved Knowledge Graphs (NODES 2024, Neo4j)](https://neo4j.com/videos/nodes-2024-entity-resolved-knowledge-graphs/)
- [Exploring RAG and GraphRAG: When and How to Use Both (Weaviate)](https://weaviate.io/blog/graph-rag)
