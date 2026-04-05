# Research Artifact -- SPEC-KB-022

> Codebase-analyse uitgevoerd op 2026-04-05.

---

## 1. Blast radius: `taxonomy_node_id` (single-label -> multi-label)

Alle bestanden die `taxonomy_node_id` als enkelvoudig veld gebruiken en aangepast moeten worden naar `taxonomy_node_ids: list[int]`:

### klai-knowledge-ingest

| Bestand | Regels | Wat er moet veranderen |
|---|---|---|
| `knowledge_ingest/qdrant_store.py:83` | Payload index op `taxonomy_node_id` (keyword) | Verwijder oude index, maak `taxonomy_node_ids` (keyword array) |
| `knowledge_ingest/qdrant_store.py:119,155-157` | `upsert_chunks()` parameter `taxonomy_node_id: int \| None` | Verander naar `taxonomy_node_ids: list[int] \| None` |
| `knowledge_ingest/qdrant_store.py:186,227-229` | `upsert_full_document()` parameter `taxonomy_node_id: int \| None` | Idem |
| `knowledge_ingest/routes/ingest.py:255,257,325,335` | Variabele `taxonomy_node_id`, doorgegeven aan `upsert_chunks()` | Verander naar `taxonomy_node_ids` (lijst van classifier) |
| `knowledge_ingest/routes/taxonomy.py:71,88,94,144` | Backfill endpoint: filter op `taxonomy_node_id`, `set_payload` met `taxonomy_node_id` | Filter op `taxonomy_node_ids`, set_payload met lijst |
| `knowledge_ingest/taxonomy_classifier.py` (geheel) | Retourneert `tuple[int \| None, float]` (single node) | Moet `list[TaxonomyNode]` retourneren (top-N met confidence) |
| `knowledge_ingest/portal_client.py:84-88` | `_fetch_from_portal` geeft `TaxonomyNode(id, name)` | Moet ook `description` meenemen |
| `knowledge_ingest/proposal_generator.py:4` | Docstring verwijst naar `taxonomy_node_id = null` | Tekstuele update |
| `tests/test_taxonomy_qdrant.py` (geheel) | Tests op `taxonomy_node_id` payload veld | Herschrijven voor `taxonomy_node_ids` als list |
| `tests/test_qdrant_link_counts.py:92` | Verwijzing naar `taxonomy_node_id` in veldlijst | Update naar `taxonomy_node_ids` |

### klai-retrieval-api

| Bestand | Regels | Wat er moet veranderen |
|---|---|---|
| `retrieval_api/models.py:17` | `taxonomy_node_ids: list[int] \| None` op RetrieveRequest | Al correct (request stuurt lijst). Filter key moet wijzigen. |
| `retrieval_api/services/search.py:181-185` | `FieldCondition(key="taxonomy_node_id", match=MatchAny(...))` | Key wijzigen naar `"taxonomy_node_ids"` |
| `tests/test_taxonomy_filter.py` (geheel) | Tests filteren op key `taxonomy_node_id` | Key wijzigen naar `taxonomy_node_ids` |

### deploy (LiteLLM hook)

| Bestand | Regels | Wat er moet veranderen |
|---|---|---|
| `deploy/litellm/klai_knowledge.py:178,216-217,370` | Gap event payload bevat `taxonomy_node_ids` uit retrieve request | Al correct (stuurt lijst door vanuit request). Geen wijziging nodig. |

### klai-portal/backend

Geen directe verwijzingen naar `taxonomy_node_id` als Qdrant-veld. Portal werkt met `PortalTaxonomyNode.id` (PostgreSQL). Geen wijzigingen nodig in portal-modellen voor de Qdrant-migratie.

**Totaal: ~15 bestanden, ~40 codelocaties.**

---

## 2. Huidig PortalTaxonomyNode model

**Bestand:** `klai-portal/backend/app/models/taxonomy.py`

```
PortalTaxonomyNode:
  id: int (PK)
  kb_id: int (FK -> portal_knowledge_bases.id)
  parent_id: int | None (FK -> self, SET NULL)
  name: str (max 128)
  slug: str (max 128)
  doc_count: int (default 0)
  sort_order: int (default 0)
  created_at: datetime
  created_by: str (max 64)
```

**Ontbrekend veld: `description: str | None`** -- cruciaal voor LLM-classificatie. De classifier krijgt nu alleen `id` en `name`; node descriptions verhogen classificatiekwaliteit aanzienlijk (zie research document sectie 11.2).

**Bestaande Alembic migraties:**
- `d6e7f8a9b0c1_add_taxonomy_tables.py` -- initieel (nodes + proposals)
- `e669581d441f_add_rls_phase2_safe_tables.py` -- RLS policies

---

## 3. Huidig PortalTaxonomyProposal model

```
PortalTaxonomyProposal:
  id: int (PK)
  kb_id: int (FK -> portal_knowledge_bases.id)
  proposal_type: str ('new_node' | 'merge' | 'split' | 'rename')
  status: str ('pending' | 'approved' | 'rejected')
  title: str (max 256)
  payload: JSONB (freeform)
  confidence_score: float | None
  created_at: datetime
  reviewed_at: datetime | None
  reviewed_by: str | None
  rejection_reason: str | None
```

Proposal model is generiek genoeg voor tag-proposals (nieuwe `proposal_type` waarden).

---

## 4. Gap detection infrastructuur

**Model:** `klai-portal/backend/app/models/retrieval_gaps.py`

```
PortalRetrievalGap:
  id: int (PK)
  org_id: int (FK -> portal_orgs.id)
  user_id: str
  query_text: str
  gap_type: str ('hard' | 'soft')
  top_score: float | None
  nearest_kb_slug: str | None
  chunks_retrieved: int
  retrieval_ms: int
  occurred_at: datetime
  resolved_at: datetime | None
```

**Ontbrekend: geen `taxonomy_node_ids` kolom.** Gaps worden opgeslagen per query maar NIET geclassificeerd naar taxonomie-nodes. Het research document identificeert dit als de hoogste editoriale waarde (sectie 10.3, Gap 4).

**Gap API endpoints** (`app/api/app_gaps.py`):
- `GET /api/app/gaps` -- lijst gaps per org, gegroepeerd op `query_text`
- `GET /api/app/gaps/summary` -- hard/soft counts (7 dagen)

Geen filtering op taxonomy node, geen aggregatie per categorie.

**Gap event flow:**
- LiteLLM hook (`deploy/litellm/klai_knowledge.py`) stuurt gap events naar portal
- Gap event bevat al `taxonomy_node_ids` als die in het retrieve request zaten (SPEC-KB-021 R6)
- Maar portal slaat dit veld NIET op -- het wordt genegeerd bij insert

---

## 5. Free tags infrastructuur

**Huidige staat:** `tags` is al een bestaand metadata-veld in de ingest pipeline:
- `routes/ingest.py:90` -- extractie uit YAML frontmatter
- `qdrant_store.py:332` -- opgenomen in `_ALLOWED_METADATA_FIELDS`
- Wordt opgeslagen in Qdrant payload als het in frontmatter staat

**NIET geindexeerd in Qdrant.** Het veld `tags` ontbreekt in de `_ensure_payload_indexes()` lijst (regel 81-84). Tags kunnen dus niet gefilterd worden bij retrieval.

**Geen governance flow.** Tags komen alleen uit YAML frontmatter. Er is geen LLM-suggestie bij ingest, geen acceptatie-flow, en geen filtering bij retrieval.

---

## 6. TaxonomyClassifier architectuur

**Bestand:** `klai-knowledge-ingest/knowledge_ingest/taxonomy_classifier.py`

**Huidige werking:**
1. Ontvangt `title`, `content_preview` (500 chars), en `list[TaxonomyNode]` (id + name)
2. Bouwt prompt met categorienamen: `"- id=5: Billing"`
3. Vraagt `klai-fast` om JSON: `{ "node_id": int|null, "confidence": float, "reasoning": str }`
4. Retourneert `tuple[int | None, float]` -- single best match
5. Timeout: 5 seconden, fallback naar `(None, 0.0)`

**Beperkingen voor V2:**
- Retourneert slechts 1 node -- documenten die meerdere categorieen raken worden beperkt
- Krijgt alleen node `name` -- geen `description` voor context
- Geen multi-label output format

**Noodzakelijke wijzigingen:**
- Prompt aanpassen: node descriptions toevoegen naast namen
- Output format: `{ "nodes": [{"node_id": int, "confidence": float}], "reasoning": str }`
- Retourtype: `list[tuple[int, float]]` -- gesorteerd op confidence, gefilterd op threshold
- Optioneel: `tags: list[str]` toevoegen aan output voor free tag suggesties

---

## 7. PortalClient -- node ophalen

**Bestand:** `klai-knowledge-ingest/knowledge_ingest/portal_client.py`

De `_fetch_from_portal()` functie haalt nodes op met `id` en `name`. Het `description` veld wordt niet meegenomen (bestaat nog niet in het model). Na toevoeging van `description` aan `PortalTaxonomyNode` moet:
- De portal internal endpoint ook `description` retourneren
- `TaxonomyNode` DTO uitgebreid worden met `description: str | None`
- De classifier prompt het description veld gebruiken
