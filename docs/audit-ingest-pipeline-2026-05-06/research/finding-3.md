# Finding 3 research: extra_payload as untyped side-channel

## Code verification

### Fields added to extra_payload (construction site: ingest.py)

All assignments happen in `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`
inside the `ingest_document` route handler, between lines 463 and 509.

| Field | Line | Source | Conditional |
|---|---|---|---|
| `title` | 463 | local var (from req or frontmatter) | Always (init) |
| `artifact_id` | 463 | pg_store.create_artifact return | Always (init) |
| `document_text` | 472 | `req.content` | `if req.content` |
| `source_type` | 474 | `req.source_type` | `if req.source_type` |
| `source_connector_id` | 476 | `req.source_connector_id` | `if req.source_connector_id` |
| `source_ref` | 478 | `req.source_ref` | `if req.source_ref` |
| `content_type` | 480 | `req.content_type` | `if req.content_type != "unknown"` |
| `assertion_mode` | 482 | `kf["assertion_mode"]` from frontmatter | Always |
| *(adapter extra fields)* | 485 | `req.extra.update(...)` — open-ended dict | `if req.extra` |
| `belief_time_start` | 487 | `kf["belief_time_start"]` from frontmatter | Always |
| `belief_time_end` | 488 | `kf["belief_time_end"]` from frontmatter | Always |
| *(frontmatter_meta fields)* | 492 | `_extract_frontmatter_metadata()` — keys: `tags`, `provenance_type`, `confidence`, `source_note` | `.update()` — always |
| `taxonomy_node_ids` | 495 | taxonomy classifier output | `if has_taxonomy` |
| `tags` | 497 | merged frontmatter + LLM tags | `if merged_tags` |
| `content_label` | 499 | LLM classifier output | Always |
| `source_label` | 501 | `_compute_source_label(req)` | Always |
| `kb_name` | 503 | `req.kb_name` | `if req.kb_name` |
| `connector_type` | 505 | `req.connector_type` | `if req.connector_type` |
| `source_domain` | 507 | `req.source_domain` | `if req.source_domain` |
| `visibility` | 509 | `kb_config.get_kb_visibility()` | Always (set last) |

**Additional fields via `req.extra` (open-ended adapter injection), set by crawl adapters:**

| Field | Set in | Conditional |
|---|---|---|
| `links_to` | `knowledge_ingest/adapters/crawler.py:311` and `routes/crawl.py:331` | `if outbound` |
| `anchor_texts` | `knowledge_ingest/adapters/crawler.py:312` and `routes/crawl.py:332` | always when crawl |
| `incoming_link_count` | `knowledge_ingest/adapters/crawler.py:313` and `routes/crawl.py:333` | always when crawl |
| `image_urls` | `knowledge_ingest/adapters/crawler.py:335` | `if image_urls` |
| `source_connector_id` | `knowledge_ingest/adapters/crawler.py:298` | if connector_id |
| `front_matter` | `knowledge_ingest/adapters/crawler.py:300` | if front_matter |
| `participants` | connector adapters (read at enrichment_tasks.py:286) | adapter-specific |

**Fields added or mutated during enrichment (enrichment_tasks.py):**

| Field | Line | When |
|---|---|---|
| `document_summary` | 325 | When not already present and document_text available |
| `document_language` | 326 | When not already present |
| `visibility` | 390 | Always — refreshed from kb_config at write time |

**Total named fields in the explicit set: 22 (ingest.py) + 6 (adapter injection) + 2 (enrichment mutation) = 30 distinct keys**, not counting `req.extra` passthrough fields which are open-ended per adapter.

### Existing pitfall

`.claude/rules/klai/projects/knowledge.md`, section **"Procrastinate enrichment passthrough (CRIT)"** (lines 221-233):

> Any metadata field set during initial ingest will be silently deleted if it is not also included in `extra_payload` before the Procrastinate job is enqueued. The enrichment worker receives only the serialized `extra_payload` dict and calls `upsert_enriched_chunks(extra_payload=extra_payload)`. It does not have access to the original ingest call's local variables. The enrichment job deletes all existing chunks and re-inserts them from `extra_payload` — so anything absent from that dict vanishes.

### Confirmed bug example

Commit `cbdfdda5` (2026-04-06, authored by Mark Vletter):

```
fix(knowledge-ingest): pass content_label through extra_payload to enrichment (SPEC-KB-023)

upsert_enriched_chunks replaces initial chunks. Without content_label in
extra_payload the field was lost after enrichment ran. Same pattern as
taxonomy_node_ids passthrough.
```

The commit touches exactly two lines of `ingest.py`. The bug was that `content_label` was computed before the Procrastinate `defer_async` call but never added to `extra_payload`. After enrichment, `upsert_enriched_chunks` deleted the initial Qdrant points and re-inserted them — `content_label` was absent from every enriched chunk. This is the third time this class of bug has occurred (the CRIT pitfall also references `taxonomy_node_ids` as a prior instance).

### Schema validation for extra_payload

No Pydantic model, TypedDict, dataclass, or any other schema exists for `extra_payload`. The type annotation at all sites is `dict` or `dict | None`:

- `ingest.py:463`: `extra_payload: dict = {...}`
- `enrichment_tasks.py:241`: `extra_payload: dict`
- `qdrant_store.py:119`: `extra_payload: dict | None = None`
- `qdrant_store.py:196`: `extra_payload: dict | None = None`
- `rebuild_tasks.py:313`: `extra_payload: dict = dict(extra)`

Mypy/pyright sees only `dict`, and nothing can statically detect a missing field.

### Tests that assert the extra_payload contract

There are **zero** tests that assert all required fields are present in `extra_payload` before `defer_async`. Existing tests touching the dict:

- `tests/test_source_label.py:70-79` — asserts `source_label` key is set after `_compute_source_label`. Tests the function, not the passthrough.
- `tests/test_anchor_text_augmentation.py` — tests the anchor augmentation logic inside `_enrich_document`, using a hand-crafted `extra_payload = {"anchor_texts": [...]}`. No assertion that this field is present in the production path.
- `tests/test_ingest_enrichment_dedup.py:74,155` — passes `extra_payload={}` as a test fixture. An empty dict.
- `tests/test_chunk_type_crawl.py:263` — passes `extra_payload={"source_type": "crawl"}`.

None of these tests verify that the full field set flows from ingest → Procrastinate task arg → `upsert_enriched_chunks`.

---

## Current behavior

The contract is enforced by nothing except the CRIT pitfall note in the rules file and developer discipline. The lifecycle is:

1. `ingest_document()` builds `extra_payload: dict` imperatively, one field at a time, with conditional guards.
2. `extra_payload` is passed verbatim as a JSON-serialized Procrastinate task argument (`defer_async(extra_payload=extra_payload, ...)`).
3. Procrastinate stores the entire argument dict as JSONB in `procrastinate_jobs.args`.
4. On Phase 2, the worker calls `_enrich_document(extra_payload=extra_payload, ...)` which deserializes back to a plain `dict`.
5. `qdrant_store.upsert_enriched_chunks` calls `await client.delete(...)` to wipe all Qdrant points for `(org_id, kb_slug, path)`, then rebuilds from `base_payload.update(extra_payload)`.
6. Any field absent from `extra_payload` at step 2 is **permanently gone** from Qdrant after step 5.

There is also a write-back mutation: `enrichment_tasks.py:325-326` adds `document_summary` and `document_language` to `extra_payload` **in memory** during the worker run. These then flow into `upsert_enriched_chunks`. If a future path reads `extra_payload` before this mutation it will see the un-enriched version.

---

## Industry standard (2026)

### Typed task payloads in mature task queue libraries

**Celery 5.5+** added first-class Pydantic support via `@app.task(pydantic=True)`. When enabled, Celery validates, deserializes, and re-serializes task arguments through the declared type annotations. A task that accepts `arg: MyModel` receives a proper `MyModel` instance; the worker validates on receipt and raises if the incoming JSON does not conform. Union types are not supported.

```python
from pydantic import BaseModel

class IngestPayload(BaseModel):
    org_id: str
    artifact_id: str
    content_label: list[str] | None = None
    visibility: str = "private"

@app.task(pydantic=True)
def enrich_document(payload: IngestPayload) -> None: ...
```

**Procrastinate** does not have native Pydantic task argument support. Its connector-level `json_dumps`/`json_loads` hooks allow a custom encoder/decoder pair to handle non-standard types (the documented example is `datetime`). A Pydantic model can be round-tripped by registering `default=lambda o: o.model_dump()` in `json_dumps` and rehydrating in `json_loads` — but this is manual and not validated on the worker side unless the task signature declares the type explicitly and a `__init_subclass__` or decorator is used to validate on entry.

**Taskiq** (2024-2025, actively maintained async-first queue) builds type-safety into the core: task functions are decorated normally with type hints and the middleware layer validates arguments on both enqueue and dequeue. It supports Pydantic models natively as task arguments.

**ARQ** does not attempt argument typing — all task functions are plain `async def`, and callers pass kwargs that are JSON-serialised. No contract enforcement.

### Typed pipeline metadata in RAG frameworks

**LlamaIndex `IngestionPipeline`**: documents pass between `TransformComponent` instances as `list[BaseNode]`. Each `BaseNode` carries a `metadata: dict[str, Any]` — identical to the pattern in klai: an open-ended dict that grows as the document flows through the pipeline. No schema enforces which keys must be present. The framework mitigates this by having transformations be pure functions (input nodes → output nodes) and by caching intermediate results via content hash, so a missing key fails loudly on first access rather than silently on re-write.

**Haystack 2.x `@component`**: uses Python type annotations directly on the `run()` method. Each component declares its input and output socket types via the `@component.output_types` decorator. The runtime validates connections (an `int` output cannot wire to a `str` input), but within each dict-typed socket the typing stops — `Document.meta: dict` is still open-ended.

**LangChain `IngestionPipeline`**: similar pattern. `Document.metadata: dict[str, Any]`. No enforcement on which keys must survive each transform.

The shared conclusion: **none of the major RAG frameworks have solved the untyped metadata propagation problem at the dict level**. The industry practice is:

1. Keep the open-ended `metadata: dict` for extensibility.
2. Document the contract in a `TypedDict` or `dataclass` that mirrors the dict shape — for documentation and mypy purposes only, not for runtime validation.
3. Add consumer-side guards (`payload.get("field", default)`) rather than producer-side enforcement.
4. Write integration tests that trace a document through the full pipeline and assert key presence at the final Qdrant/vector store write.

### Schema evolution for in-flight tasks

When adding a field to a typed payload between a producer deploy and consumer deploy:

- **Additive fields**: declare as `Optional[T] = None` in the typed model. Workers on old code ignore the field (JSON decoding drops unknown keys if `model_config = ConfigDict(extra="ignore")`). Workers on new code receive `None` and apply default behavior. Safe to deploy producer-side first.
- **Removing fields**: mark as deprecated, keep in model for one deploy cycle, then remove. Workers on new code that receive old messages with the extra field silently ignore it if `extra="ignore"`.
- **Renaming fields**: treat as additive (new name) + removal (old name). One full deploy cycle required for safe transition.
- **Changing type**: not safe for in-flight tasks. Requires a version discriminator or flush of the queue before deploy.

The key pattern for Procrastinate specifically: tasks serialised as JSONB are stored at enqueue time. Workers that restart during a queue drain will see a mix of old and new serializations. `Optional` fields with safe defaults handle this correctly; required fields without defaults cause `ValidationError` on old messages.

---

## Fix recommendations

### Option A: TypedDict (minimal friction, immediate value)

Define a `TypedDict` that documents every field that must flow through `extra_payload`. No runtime overhead, mypy validates producer and consumer sites.

```python
# knowledge_ingest/models.py or knowledge_ingest/payload_types.py
from typing import TypedDict, NotRequired

class EnrichmentPayload(TypedDict):
    # Core — always present
    title: str
    artifact_id: str
    assertion_mode: str
    belief_time_start: NotRequired[int | None]
    belief_time_end: NotRequired[int | None]
    visibility: str
    content_label: NotRequired[list[str] | None]
    source_label: str
    # Connector provenance — connector-sourced documents only
    source_type: NotRequired[str]
    source_connector_id: NotRequired[str]
    source_ref: NotRequired[str]
    connector_type: NotRequired[str]
    source_domain: NotRequired[str]
    kb_name: NotRequired[str]
    # Document content
    document_text: NotRequired[str]
    content_type: NotRequired[str]
    # Taxonomy
    taxonomy_node_ids: NotRequired[list[int]]
    tags: NotRequired[list[str]]
    # Crawler-specific (injected via req.extra)
    links_to: NotRequired[list[str]]
    anchor_texts: NotRequired[list[str]]
    incoming_link_count: NotRequired[int]
    image_urls: NotRequired[list[str]]
    # Enrichment-mutated fields
    document_summary: NotRequired[str]
    document_language: NotRequired[str]
    # Frontmatter passthrough
    provenance_type: NotRequired[str]
    confidence: NotRequired[float]
    source_note: NotRequired[str]
    participants: NotRequired[list[dict]]
```

Change `extra_payload: dict` to `extra_payload: EnrichmentPayload` at all sites. The `update()` calls with adapter `extra` remain untyped — that is an acceptable residual risk given adapters can add arbitrary keys.

**Migration**: purely additive. No runtime change, no Procrastinate config change, no in-flight task impact.

### Option B: Pydantic BaseModel with custom Procrastinate serializer (full runtime validation)

Define a Pydantic model and register a custom `json_dumps`/`json_loads` pair on the Procrastinate connector. This gives full validation on both producer and consumer sides.

```python
# knowledge_ingest/payload_types.py
from pydantic import BaseModel, ConfigDict

class EnrichmentPayload(BaseModel):
    model_config = ConfigDict(extra="allow")  # Allow adapter-injected fields

    title: str
    artifact_id: str
    visibility: str = "private"
    assertion_mode: str = "unknown"
    content_label: list[str] | None = None
    source_label: str = ""
    # ... all fields with safe defaults ...

# knowledge_ingest/app.py or connector config
import functools, json
from knowledge_ingest.payload_types import EnrichmentPayload

def _json_dumps(obj, **kwargs):
    if isinstance(obj, EnrichmentPayload):
        return obj.model_dump_json()
    return json.dumps(obj, **kwargs)

def _json_loads(s):
    d = json.loads(s)
    # Only rehydrate if the dict has the discriminator key
    if "artifact_id" in d and "title" in d:
        return EnrichmentPayload.model_validate(d)
    return d
```

Then pass `json_dumps=_json_dumps, json_loads=_json_loads` to the Procrastinate connector.

**Limitations**: rehydration via `object_hook` applies to all decoded dicts, not just task args. The global `object_hook` approach is fragile — Procrastinate's JSONB args are nested under a top-level `{"args": {...}}` structure. A safer pattern is to keep `extra_payload` as `dict` in the Procrastinate signature and validate inside `_enrich_document` at the top of the function body.

**Recommended variant (less fragile)**: keep the Procrastinate task signature as `extra_payload: dict`, validate inside the task body:

```python
async def _enrich_document(..., extra_payload: dict, ...) -> None:
    payload = EnrichmentPayload.model_validate(extra_payload)
    # use payload.source_connector_id, payload.content_label, etc.
```

This gives runtime validation on every task execution without touching serialization infrastructure. Failed validations raise `ValidationError` which Procrastinate catches and marks the job as failed — visible in `procrastinate_jobs.status = 'failed'`.

**Migration for in-flight tasks**: Procrastinate jobs currently in the queue have `extra_payload` as a plain dict with optional keys. The Pydantic model must use `Optional` + safe defaults for all non-required fields, and `model_config = ConfigDict(extra="allow")` to absorb unknown adapter keys. Zero backward-compatibility break.

### Option C: Explicit field forwarding with a validation test (low-cost safety net, no type change)

Keep `extra_payload: dict` everywhere. Add a single pytest fixture that constructs a realistic `extra_payload` (exercising all code paths: crawl, connector, upload) and asserts that after a simulated `upsert_enriched_chunks`, every expected field is present in the returned Qdrant payload. This is the pattern used by the retrieval-api consumer tests added after the `X-Caller-Service` incident.

```python
# tests/test_extra_payload_contract.py
REQUIRED_FIELDS = [
    "title", "artifact_id", "visibility", "assertion_mode",
    "content_label", "source_label",
]

def test_crawl_extra_payload_has_required_fields():
    payload = build_crawl_extra_payload(...)
    for field in REQUIRED_FIELDS:
        assert field in payload, f"Missing required field: {field}"
```

This catches the class of bug that hit `content_label` and `taxonomy_node_ids` without any code restructuring.

### Recommended approach

Start with **Option C** immediately (one test file, zero risk) as a regression guard. Pursue **Option A** (TypedDict) in the next SPEC as a static analysis upgrade that makes future field additions self-documenting. Reserve **Option B** (Pydantic validation inside the task) for when the pipeline adds a new field that has a required invariant (e.g., must be non-empty, must be a valid UUID) — at that point the TypedDict is insufficient and runtime validation earns its cost.

Do not pursue a custom Procrastinate `json_dumps`/`json_loads` serializer for this use case. The complexity of a global object_hook that must distinguish EnrichmentPayload dicts from other nested dicts in the JSONB structure is disproportionate to the benefit.

---

## Risk assessment

**Probability of next bug**: High. The pattern has produced at least three confirmed incidents (connector_id absent for crawl chunks, taxonomy_node_ids absent before SPEC-CRAWLER-005 fix, content_label absent fixed in commit `cbdfdda5`). Each new metadata field added to the pipeline — and the audit identified 30+ current fields — must be remembered individually by the developer. There is no mechanical check.

**Blast radius when it occurs**: High. `upsert_enriched_chunks` unconditionally deletes and re-inserts. A field absent from `extra_payload` disappears from 100% of enriched chunks for that document permanently, with no error logged. Discovery requires manual Qdrant inspection.

**Cost of refactor**:
- Option C (test): Priority Low effort — 1 test file, no production code change.
- Option A (TypedDict): Priority Medium effort — 1 new file + type annotation changes at ~8 call sites. No runtime change.
- Option B (Pydantic validation in task body): Priority Medium effort — 1 new model class + 1 validation call in `_enrich_document`. Requires all existing fields to have safe defaults.

**Schema evolution risk**: None for options A and C. For option B, all new fields must declare `Optional` defaults; required fields are only safe when every caller already sends them.

---

## References

- Procrastinate custom JSON encoder/decoder: [https://procrastinate.readthedocs.io/en/stable/howto/advanced/custom_json_encoder_decoder.html](https://procrastinate.readthedocs.io/en/stable/howto/advanced/custom_json_encoder_decoder.html)
- Celery 5.5+ Pydantic task argument support: [https://docs.celeryq.dev/en/stable/userguide/tasks.html#task-serialization](https://docs.celeryq.dev/en/stable/userguide/tasks.html#task-serialization)
- Celery Pydantic preserializers discussion: [https://dosu.dev/blog/celery-preserializers-a-low-friction-path-to-pydantic-support](https://dosu.dev/blog/celery-preserializers-a-low-friction-path-to-pydantic-support)
- LlamaIndex TransformComponent and metadata typing: [https://developers.llamaindex.ai/python/framework/module_guides/loading/ingestion_pipeline/transformations/](https://developers.llamaindex.ai/python/framework/module_guides/loading/ingestion_pipeline/transformations/)
- Haystack 2.x custom components and typed IO: [https://docs.haystack.deepset.ai/docs/custom-components](https://docs.haystack.deepset.ai/docs/custom-components)
- Taskiq type-safe async task queue: [https://github.com/taskiq-python/taskiq](https://github.com/taskiq-python/taskiq)
- Pydantic v2 serialization: [https://docs.pydantic.dev/2.11/concepts/serialization/](https://docs.pydantic.dev/2.11/concepts/serialization/)
