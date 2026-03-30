# SPEC-KB-006: Content-Type-Aware Enrichment and Intake Adapters

> Status: COMPLETED (2026-03-26)
> Author: Mark Vletter (design) + Claude (SPEC)
> Builds on: SPEC-KB-004 (knowledge schema), SPEC-KB-005 (contextual retrieval)
> Architecture reference: `claude-docs/klai-knowledge-architecture.md` SS4.2, SS12
> Created: 2026-03-26
> Completed: 2026-03-26 — PR #32 merged (GetKlai/klai)

---

## What exists today

SPEC-KB-005 introduced LLM enrichment (contextual prefix + HyPE questions) between chunking and embedding. The enrichment module uses a single heuristic to decide whether to populate `vector_questions`: `synthesis_depth <= 1`. KB-005 itself flags this as a known limitation:

> `synthesis_depth` is a KB-biased proxy for vocabulary gap. [...] The long-term signal is content-type (via the adapter that produced the artifact), not synthesis_depth.

The current enrichment module also hardcodes a single context extraction strategy: first-N tokens of the document. KB-005 acknowledges this is wrong for transcripts and email threads, where relevant context is mid-document or most-recent.

There are no intake adapters. All content enters via the generic `POST /ingest/v1/document` endpoint, which accepts pre-extracted text. There is no mechanism to ingest transcripts from Scribe or crawl external knowledge bases.

The `knowledge.artifacts` table has no `content_type` field. The `extra` JSONB field does not exist. There is no way to store adapter-specific metadata (participant lists, source URLs, crawl timestamps) alongside an artifact.

---

## What this SPEC builds

Two things: a content-type-aware enrichment parameter system (replacing the `synthesis_depth <= 1` heuristic), and three intake adapters (meeting transcripts via Vexa, Scribe audio transcripts, web crawler).

### Part 1: Content-type parameter profiles

A `ContentTypeProfile` dataclass defines enrichment behavior per content type. The `content_type` field is added to `knowledge.artifacts` and set by the adapter (or defaults to `unknown` for generic ingest). The enrichment task in `enrichment_tasks.py` looks up the profile instead of checking `synthesis_depth <= 1`.

Each profile specifies:
- Whether HyPE questions are embedded as `vector_questions` (and under what condition)
- Which context extraction strategy to use (a named function, not a parameter)
- How many context tokens to extract
- Target chunk size range
- What HyPE questions should focus on (prompt instruction)

### Part 2: Three intake adapters

1. **Meeting adapter (Vexa)** -- ingests meeting transcripts from VexaMeeting records (Vexa bot joins a meeting call and produces diarized segments)
2. **Scribe adapter** -- ingests audio upload and recording transcripts from the scribe-api (Whisper-based, no speaker labels)
3. **Web crawler adapter** -- crawls external knowledge bases and PDFs for bulk ingestion

All three adapters produce artifacts with a `content_type` and adapter-specific metadata in a new `extra` JSONB column on `knowledge.artifacts`, then call the existing ingest pipeline.

---

## Design decisions

### D1: Content-type lives on the artifact, not the chunk

`content_type` is a property of the source document, not of individual chunks. A meeting transcript is a meeting transcript regardless of which chunk you look at. This matches the existing pattern where `provenance_type`, `assertion_mode`, and `synthesis_depth` are artifact-level (SPEC-KB-004 D1).

The field is added to `knowledge.artifacts` as `content_type TEXT NOT NULL DEFAULT 'unknown'`. Valid values: `kb_article`, `meeting_transcript`, `1on1_transcript`, `email_thread`, `pdf_document`, `unknown`. The CHECK constraint is deliberately omitted -- new content types should not require a schema migration. Validation happens in application code.

### D2: Parameter profiles are code, not database config

Profiles are defined as a Python dict of `ContentTypeProfile` dataclasses in `enrichment.py`, not in a database table. Rationale:

- Profiles change with the enrichment prompt and context strategy -- they are code, not user configuration
- Per-org overrides remain in `knowledge.org_config.extra_config` JSONB (already exists from KB-005) for the rare case where an org needs non-default behavior
- Adding a new content type means adding an adapter + a profile in the same PR -- atomic and reviewable

### D3: Context extraction is a strategy pattern, not a parameter

Different content types need fundamentally different document context for enrichment. A transcript needs the preceding speaker turns around the current chunk; a PDF needs the title page and table of contents; an email thread needs the most recent messages. These are not parameterizable variants of the same function -- they are different algorithms.

Four named strategies:

| Strategy | Function | Used by | What it extracts |
|---|---|---|---|
| `first_n` | `extract_first_n_tokens(doc, n)` | `kb_article`, `unknown` | First N tokens (intro + headings) |
| `rolling_window` | `extract_rolling_window(doc, chunk_index, n)` | `meeting_transcript`, `1on1_transcript` | Preceding speaker turns around current chunk |
| `most_recent` | `extract_most_recent_messages(doc, n)` | `email_thread` | Most recent N messages in thread |
| `front_matter` | `extract_front_matter(doc, n)` | `pdf_document` | Title + TOC extracted during PDF parsing |

The enrichment task passes `chunk_index` to the strategy function so position-dependent strategies (rolling window) know where in the document the current chunk sits. The `first_n` strategy (current KB-005 behavior) ignores `chunk_index`.

### D4: HyPE is conditional per content type, not binary

The KB-005 heuristic (`synthesis_depth <= 1` = HyPE, otherwise no HyPE) is replaced with a per-profile decision:

| content_type | HyPE behavior | Rationale |
|---|---|---|
| `kb_article` | Conditional: only when `synthesis_depth <= 1` (register gap detected) | Curated KB articles at depth 3-4 have small vocabulary gap; raw imports at depth 0-1 do not |
| `meeting_transcript` | Always | Transcripts have large vocabulary gap -- colloquial speech vs. search queries |
| `1on1_transcript` | Always | Same rationale as meetings; fewer speakers but same register gap |
| `email_thread` | Conditional: only when `synthesis_depth <= 1` | Formal email has smaller gap than raw forwarded chains |
| `pdf_document` | Always | Technical PDFs have domain-specific vocabulary that benefits from question expansion |
| `unknown` | Never (fallback) | Cannot optimize what we do not understand; raw embedding is safest |

The profile stores a callable `hype_enabled(synthesis_depth: int) -> bool` rather than a boolean, allowing the conditional logic to vary per type.

### D5: Chunk size ranges are advisory, not enforced by the profile system

The profile specifies `chunk_tokens_min` and `chunk_tokens_max` as guidance for the chunker. The actual chunking logic remains in `chunker.py` -- the profile does not replace the chunker. For transcripts, chunking is done by the adapter (speaker-turn clusters), not by `chunker.chunk_markdown`. The profile's chunk size range is used by the adapter to decide cluster size.

### D6: Meeting adapter uses Vexa speaker segments; Scribe adapter uses recording_type

Two separate detection mechanisms for two separate systems:

- **Meeting adapter (Vexa)**: Speaker labels come directly from Vexa's diarized transcript segments (`{speaker: "Mark Vletter", text: "...", start: 0.0, end: 4.2}`). `content_type` is always `meeting_transcript`. Meetings are always multi-party -- Vexa bot joins a meeting call and captures speaker-attributed audio. No heuristic is needed.

- **Scribe adapter**: No speaker labels available. Whisper (used by Scribe) does not perform speaker diarization. `content_type` is determined by the `recording_type` field the user provides when summarizing: `"meeting"` → `meeting_transcript`, `"recording"` → `1on1_transcript`. This field already exists in the Scribe API.

The earlier assumption that "Scribe provides speaker-diarized transcripts" was incorrect. Whisper does not perform speaker diarization. Vexa (the meeting bot) does, because it captures audio per-participant from the meeting platform.

### D7: Meeting adapter chunks by speaker-turn clusters; Scribe adapter chunks by segment clusters

Transcript chunking by fixed token windows breaks mid-sentence and mid-turn, destroying speaker attribution or segment coherence. Both transcript adapters produce pre-chunked text and pass it directly to the ingest pipeline with `skip_chunking=True` (a new flag on the ingest request model). The chunker in `chunker.py` is not used for transcripts.

**Meeting adapter (Vexa)**: Groups consecutive speaker turns into clusters of 3-5 turns per chunk. Speaker labels are available from Vexa's diarized segments, so each chunk preserves speaker attribution. Respects the profile's `chunk_tokens_max` as a soft ceiling (max 400 tokens per chunk).

**Scribe adapter**: Groups consecutive Whisper segments into clusters of 3-5 segments per chunk using time-based boundaries. No speaker attribution is available. Falls back to paragraph splitting of `text` when `segments_json` is absent.

### D8: Web crawler uses crawl4ai, not Playwright

For bulk web crawling, `crawl4ai` (async Python crawler built on aiohttp) provides:
- Async page fetching with configurable concurrency
- robots.txt respect out of the box
- Clean text extraction (nav/footer/ad removal)
- No browser process overhead (unlike Playwright/Selenium)

Playwright is appropriate for JS-rendered single pages; for bulk crawl of static knowledge bases, it is overkill and a deployment burden (browser binaries in the Docker image).

### D9: Crawler is a background job, not a synchronous endpoint

The crawler processes potentially hundreds of pages. The API endpoint `POST /knowledge/v1/crawl` accepts a crawl configuration and enqueues a Procrastinate bulk job. It returns immediately with a job ID. The crawler job:
- Fetches pages sequentially (respecting rate limit, default 2/s)
- Per page: extracts text, detects PDF vs. HTML, sets `content_type`
- Per page: calls the existing ingest endpoint internally (not over HTTP -- direct function call)
- Reports progress to a `knowledge.crawl_jobs` status table

### D10: Extra JSONB column for adapter-specific metadata

A new `extra JSONB NOT NULL DEFAULT '{}'` column on `knowledge.artifacts` stores adapter-specific metadata that does not warrant its own column:

- Scribe: `{"participants": [{"name": "...", "role": "..."}], "recording_duration_seconds": 3600}`
- Crawler: `{"source_url": "https://...", "crawled_at": 1711497600}`

This follows the same pattern as `knowledge.org_config.extra_config` (KB-005 D5) -- extensible without schema migration. The `extra` field is also propagated to the Qdrant payload so it is available at retrieval time.

### D11: Pronoun resolution is an enrichment prompt concern, not an adapter concern

The Scribe adapter does NOT attempt pronoun resolution ("she said" --> "Alice said"). This is handled by the enrichment LLM prompt, which receives participant metadata from the `extra` field. The prompt instruction for transcript content types includes: "Gebruik de deelnemerslijst om voornaamwoorden op te lossen waar mogelijk."

This keeps the adapter simple (data extraction only) and leverages the LLM's language understanding for the hard part.

### D12: Sparse vectors via FlagEmbedding sidecar, not TEI

TEI only exposes sparse output for BERT/DistilBERT MaskedLM architectures via `--pooling splade`. BGE-M3 uses an XLM-RoBERTa architecture and is excluded from this flag. A lightweight FlagEmbedding FastAPI sidecar is added alongside the existing TEI service.

Why sparse vectors matter: dense embeddings miss exact lexical matches for product names, ticket numbers, internal abbreviations, and proper nouns -- endemic to organizational knowledge. Hybrid dense+sparse retrieval yields +9 nDCG@10 on BEIR benchmarks on average, up to +24% on niche domains with internal jargon.

The sidecar:
- Uses `BAAI/bge-m3` via the `FlagEmbedding` library -- same model, same weights as TEI
- Exposes `POST /embed_sparse` returning `{"indices": [...], "values": [...]}`
- Runs with `return_sparse=True, return_dense=False` -- sparse only, no duplication with TEI
- GPU optional; CPU is feasible for batch ingest workloads

Sparse vector format in Qdrant: `SparseVector(indices=[...], values=[...])` -- no fixed dimensionality (vocabulary-driven, 250k-dim BGE-M3 tokenizer). A typical document produces approximately 80 non-zero entries, approximately 640 bytes -- 15-20% of the dense vector size.

Every Qdrant point now carries three named vectors:

| Named vector | Content | When populated |
|---|---|---|
| `vector_chunk` | BGE-M3 dense of enriched text | Always |
| `vector_sparse` | BGE-M3 sparse token weights | Always |
| `vector_questions` | Aggregated HyPE question embedding | depth 0-1 chunks only |

Qdrant collection configuration:

```python
client.create_collection(
    collection_name="klai_knowledge_v2",
    vectors_config={
        "vector_chunk": VectorParams(size=1024, distance=Distance.COSINE),
        "vector_questions": VectorParams(size=1024, distance=Distance.COSINE),
    },
    sparse_vectors_config={
        "vector_sparse": SparseVectorParams(index=SparseIndexParams(on_disk=False))
    }
)
```

### D13: Whisper segments preserved as structured transcript for knowledge ingestion

The whisper-server currently discards segment timestamps: the transcription code does `text_parts = [seg.text for seg in segments]`, losing all `start`/`end` boundaries. For knowledge ingestion, segment boundaries are the correct chunking unit -- they represent natural speech pauses as determined by the ASR model and are more accurate chunk boundaries than fixed-token splits.

Change to whisper-server response: add a `segments` field alongside `text`:

```json
{
  "text": "full concatenated text",
  "language": "nl",
  "duration": 1823.4,
  "segments": [
    {"start": 0.0, "end": 4.2, "text": "Goed, laten we beginnen."},
    {"start": 4.8, "end": 9.1, "text": "Het eerste agendapunt is..."}
  ]
}
```

The scribe-api stores segments in a new `segments_json` column on `scribe.transcriptions` (nullable, for backward compatibility with existing records).

The Scribe knowledge adapter uses `segments_json` when available, falling back to paragraph-split of `text` when not. The adapter clusters 3-5 consecutive segments into one chunk, targeting 150-400 tokens per chunk (consistent with D7 and Bevinding 14 from KB-005 research).

Note: faster-whisper segments do NOT include speaker labels. The Scribe adapter uses time-based segment clustering without speaker attribution. The meeting adapter (Vexa) does have speaker labels -- see D6.

### D14: Meeting extraction prompt enriched for knowledge-grade structured output

The current `_MEETING_EXTRACTION_SYSTEM` prompt in scribe-api extracts 6 fields. For organizational knowledge ingestion, decisions without rationale and action items without deadlines are low-quality knowledge artifacts -- they answer "what was decided" but not "why" or "by when".

Updated extraction schema:

```json
{
  "speakers_present": ["name1", "name2"],
  "topics": ["topic1"],
  "decisions": [
    {"decision": "...", "rationale": "... or null", "decided_by": "... or null"}
  ],
  "action_items": [
    {"owner": "... or null", "task": "...", "deadline": "... or null"}
  ],
  "commitments": [
    {"speaker": "...", "commitment": "..."}
  ],
  "key_quotes": ["verbatim sentence worth preserving"],
  "open_questions": ["question1"],
  "next_steps": ["step1"]
}
```

Changes vs. current schema:
- `decisions`: upgraded from `string[]` to `object[]` with `rationale` and `decided_by` fields
- `action_items`: added `deadline` field (null if not mentioned)
- `commitments`: new -- explicit personal commitments separate from general action items
- `key_quotes`: new -- verbatim statements worth preserving exactly

The `summary_json.structured` field in the scribe-api response is updated to reflect the new schema. Backward compatibility: existing summaries in the DB retain the old schema; frontend `summary_json` rendering must handle both shapes (old flat strings in `decisions[]`, new objects).

### D15: Qdrant point payloads include temporal and filter fields

Every Qdrant point must include `content_type`, `valid_from`, `valid_until`, and `ingested_at` in its payload. These fields come from the artifact's bi-temporal metadata in PostgreSQL (KB-005 schema) and are passed through at ingest time.

`content_type` as a payload field enables post-retrieval and pre-retrieval filtering: `Filter(must=[FieldCondition(key="content_type", match=MatchValue(value="meeting_transcript"))])` -- the same Qdrant filter mechanism already used for `kb_slug` and `org_id`.

The temporal fields (`valid_from`, `valid_until`) enable bi-temporal filtering at retrieval time (KB-005 D3). `ingested_at` is a recency fallback for queries that should prefer recently ingested content when other signals are equal.

```python
payload = {
    # existing fields
    "text": original_text,
    "text_enriched": enriched_text,
    "kb_slug": kb_slug,
    "path": path,
    "org_id": org_id,
    "artifact_id": artifact_id,
    "content_type": content_type,        # NEW -- enables content_type filtering
    # temporal fields -- NEW
    "valid_from": belief_time_start,     # Unix timestamp, for bi-temporal filter
    "valid_until": belief_time_end,      # Unix timestamp, null = still valid
    "ingested_at": int(time.time()),     # for recency fallback
}
```

---

## Content-type parameter profiles

```python
# knowledge_ingest/content_profiles.py
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class ContentTypeProfile:
    content_type: str
    hype_enabled: Callable[[int], bool]  # (synthesis_depth) -> should embed vector_questions
    context_strategy: str                 # name of extraction function
    context_tokens_min: int
    context_tokens_max: int
    chunk_tokens_min: int
    chunk_tokens_max: int
    hype_question_focus: str              # prompt instruction for question generation

PROFILES: dict[str, ContentTypeProfile] = {
    "kb_article": ContentTypeProfile(
        content_type="kb_article",
        hype_enabled=lambda depth: depth <= 1,
        context_strategy="first_n",
        context_tokens_min=800,
        context_tokens_max=2000,
        chunk_tokens_min=300,
        chunk_tokens_max=500,
        hype_question_focus="Genereer vragen in alledaagse taal die een gebruiker zou typen — herformuleringen, synoniemen, informele varianten.",
    ),
    "meeting_transcript": ContentTypeProfile(
        content_type="meeting_transcript",
        hype_enabled=lambda depth: True,
        context_strategy="rolling_window",
        context_tokens_min=600,
        context_tokens_max=1200,
        chunk_tokens_min=150,
        chunk_tokens_max=400,
        hype_question_focus="Genereer vragen over beslissingen, actiepunten, eigenaren en deadlines die in dit fragment besproken worden.",
    ),
    "1on1_transcript": ContentTypeProfile(
        content_type="1on1_transcript",
        hype_enabled=lambda depth: True,
        context_strategy="rolling_window",
        context_tokens_min=400,
        context_tokens_max=800,
        chunk_tokens_min=100,
        chunk_tokens_max=300,
        hype_question_focus="Genereer vragen over toezeggingen, besproken onderwerpen en genoemde namen.",
    ),
    "email_thread": ContentTypeProfile(
        content_type="email_thread",
        hype_enabled=lambda depth: depth <= 1,
        context_strategy="most_recent",
        context_tokens_min=1000,
        context_tokens_max=4000,
        chunk_tokens_min=200,
        chunk_tokens_max=500,
        hype_question_focus="Genereer vragen over de status, beslissingen en verzoeken in deze e-mailthread.",
    ),
    "pdf_document": ContentTypeProfile(
        content_type="pdf_document",
        hype_enabled=lambda depth: True,
        context_strategy="front_matter",
        context_tokens_min=800,
        context_tokens_max=2000,
        chunk_tokens_min=400,
        chunk_tokens_max=800,
        hype_question_focus="Genereer how-to vragen, definitie-vragen en specificatie-vragen die dit fragment beantwoordt.",
    ),
    "unknown": ContentTypeProfile(
        content_type="unknown",
        hype_enabled=lambda depth: False,
        context_strategy="first_n",
        context_tokens_min=2000,
        context_tokens_max=2000,
        chunk_tokens_min=500,
        chunk_tokens_max=500,
        hype_question_focus="",
    ),
}

def get_profile(content_type: str) -> ContentTypeProfile:
    return PROFILES.get(content_type, PROFILES["unknown"])
```

---

## Changes to `knowledge-ingest`

### Migration: `005_knowledge_artifacts_content_type.sql`

```sql
-- Migration: 005_knowledge_artifacts_content_type.sql
-- Adds content_type and extra fields to knowledge.artifacts.
-- Safe: additive columns with defaults, no data loss.

ALTER TABLE knowledge.artifacts
    ADD COLUMN IF NOT EXISTS content_type TEXT NOT NULL DEFAULT 'unknown';

ALTER TABLE knowledge.artifacts
    ADD COLUMN IF NOT EXISTS extra JSONB NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_artifacts_content_type
    ON knowledge.artifacts(content_type);
```

> Note: migration numbering assumes KB-005's `004_knowledge_org_config.sql` is already in place.

### Migration: `006_knowledge_crawl_jobs.sql`

```sql
-- Migration: 006_knowledge_crawl_jobs.sql
-- Tracks web crawler job status.

CREATE TABLE IF NOT EXISTS knowledge.crawl_jobs (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL,
    kb_slug     TEXT NOT NULL,
    config      JSONB NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','running','completed','failed')),
    pages_total INTEGER NOT NULL DEFAULT 0,
    pages_done  INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  BIGINT NOT NULL,
    updated_at  BIGINT NOT NULL
);
```

### New: `content_profiles.py` -- parameter profiles

As shown above. Defines `ContentTypeProfile`, the `PROFILES` dict, and `get_profile()`.

### New: `context_strategies.py` -- context extraction functions

```python
# knowledge_ingest/context_strategies.py

def extract_first_n_tokens(doc: str, n: int, **kwargs) -> str:
    """First N tokens of the document. Used for articles and unknown types."""
    # Existing logic from enrichment.py, extracted into a named function.
    ...

def extract_rolling_window(doc: str, n: int, *, chunk_index: int, **kwargs) -> str:
    """Preceding speaker turns around the current chunk position.
    For transcripts where context is positional, not front-loaded."""
    ...

def extract_most_recent_messages(doc: str, n: int, **kwargs) -> str:
    """Most recent N messages from an email thread.
    Email threads have context at the end (most recent reply), not the beginning."""
    ...

def extract_front_matter(doc: str, n: int, *, front_matter: str | None = None, **kwargs) -> str:
    """Title + TOC extracted during PDF parsing.
    Falls back to first_n if no front_matter is provided."""
    if front_matter:
        return front_matter[:n * 4]  # approximate token-to-char ratio
    return extract_first_n_tokens(doc, n)

STRATEGIES: dict[str, callable] = {
    "first_n": extract_first_n_tokens,
    "rolling_window": extract_rolling_window,
    "most_recent": extract_most_recent_messages,
    "front_matter": extract_front_matter,
}
```

### Updated: `enrichment_tasks.py` -- profile-based enrichment

Replace the `synthesis_depth <= 1` check with profile lookup:

```python
# In _enrich_document():
from knowledge_ingest.content_profiles import get_profile
from knowledge_ingest.context_strategies import STRATEGIES

profile = get_profile(content_type)  # content_type passed as new parameter
strategy_fn = STRATEGIES[profile.context_strategy]

for i, chunk in enumerate(chunks):
    doc_context = strategy_fn(
        document_text,
        profile.context_tokens_max,
        chunk_index=i,
        front_matter=extra_payload.get("front_matter"),
    )
    enriched = await enrich_chunk(
        document_text=doc_context,
        chunk_text=chunk,
        title=title,
        path=path,
        question_focus=profile.hype_question_focus,
    )
    # ...
    embed_questions = profile.hype_enabled(synthesis_depth)
```

The task signature gains a `content_type: str` parameter. The `enrichment.py` prompt template gains an optional `question_focus` field injected from the profile.

### Updated: `enrichment.py` -- question focus in prompt

```python
ENRICHMENT_PROMPT = """
Documenttitel: {title}
Pad: {path}

<document>
{document_text}
</document>

<chunk>
{chunk_text}
</chunk>

{participant_context}

Genereer een JSON-object met:
- "context_prefix": een zin van 1-2 regels die deze chunk plaatst binnen het document
  (welk document, welk onderwerp, welke sectie).
- "questions": 3-5 vragen die deze chunk beantwoordt.
  {question_focus}

Antwoord ALLEEN met geldig JSON.
"""
```

The `participant_context` block is populated from `extra.participants` for transcript content types: "Deelnemers: Alice (Product Owner), Bob (Engineer). Gebruik de deelnemerslijst om voornaamwoorden op te lossen waar mogelijk."

### Updated: `models.py` -- ingest request gains `content_type` and `skip_chunking`

```python
class IngestRequest(BaseModel):
    # ... existing fields ...
    content_type: str = "unknown"
    skip_chunking: bool = False  # True when adapter provides pre-chunked text
    extra: dict = {}             # adapter-specific metadata
```

### Updated: `ingest.py` -- pass content_type through the pipeline

In `ingest_document()`:
1. Pass `content_type` to `pg_store.create_artifact()` (stored in new column)
2. Pass `extra` to `pg_store.create_artifact()` (stored in new column)
3. If `skip_chunking` is True, use `req.content` as pre-chunked list (adapter already chunked)
4. Pass `content_type` to the Procrastinate enrichment task

### New: `adapters/meeting.py` -- Meeting (Vexa) adapter

```python
# knowledge_ingest/adapters/meeting.py

from knowledge_ingest.ingest import ingest_document
from knowledge_ingest.models import IngestRequest

async def ingest_vexa_meeting(
    org_id: str,
    kb_slug: str,
    meeting_id: int,  # VexaMeeting ID from portal-api DB
) -> str:
    """
    Process a VexaMeeting transcript into the knowledge pipeline.

    Steps:
    1. Read transcript_segments (JSONB) from portal.vexa_meetings via portal-api
    2. Cluster segments by speaker turn into chunks (3-5 turns, max 400 tokens)
    3. Call ingest_document with skip_chunking=True and content_type="meeting_transcript"

    content_type is always "meeting_transcript" -- meetings are always multi-party.
    Speaker labels are available from Vexa's diarized segments.
    """
    meeting = await _fetch_vexa_meeting(meeting_id)
    segments = meeting["transcript_segments"]  # list of {start, end, text, speaker, ...}
    chunks = _chunk_by_speaker_turns(segments, max_tokens=400)
    participants = _extract_participants(segments)
    full_text = " ".join(seg["text"] for seg in segments)

    return await ingest_document(IngestRequest(
        org_id=org_id,
        kb_slug=kb_slug,
        path=f"meeting/{meeting_id}",
        content=full_text,
        title=meeting.get("meeting_title", "Untitled meeting"),
        source_type="connector",
        content_type="meeting_transcript",
        skip_chunking=True,
        synthesis_depth=0,
        extra={
            "participants": participants,
            "platform": meeting.get("platform"),
            "meeting_title": meeting.get("meeting_title"),
            "meeting_id": meeting_id,
        },
    ))

def _chunk_by_speaker_turns(segments: list[dict], max_tokens: int = 400) -> list[str]:
    """Group consecutive speaker turns into clusters of 3-5 turns.
    Speaker labels are available from Vexa segments ({speaker: "Name", text: "..."}).
    Respects max_tokens as a soft ceiling -- never splits mid-turn."""
    ...
```

### New: `adapters/scribe.py` -- Scribe audio transcript adapter

```python
# knowledge_ingest/adapters/scribe.py

from knowledge_ingest.ingest import ingest_document
from knowledge_ingest.models import IngestRequest

async def ingest_scribe_transcript(
    org_id: str,
    kb_slug: str,
    transcription_id: int,  # scribe.transcriptions row ID
) -> str:
    """
    Process a Scribe transcription into the knowledge pipeline.

    Steps:
    1. Read transcription record from scribe.transcriptions (via scribe-api)
    2. Detect content_type from recording_type field ("meeting" or "recording")
    3. Chunk by segment clusters using segments_json when available;
       fall back to paragraph splitting of text when not
    4. Call ingest_document with skip_chunking=True

    No speaker labels available -- Whisper does not diarize.
    content_type is authoritative from recording_type, no speaker count heuristic.
    """
    transcription = await _fetch_scribe_transcription(transcription_id)
    content_type = _detect_content_type(transcription)
    chunks = _chunk_by_segments(transcription, max_tokens=400)
    full_text = transcription["text"]

    return await ingest_document(IngestRequest(
        org_id=org_id,
        kb_slug=kb_slug,
        path=f"klai-scribe/{transcription_id}",
        content=full_text,
        title=transcription.get("title", "Untitled recording"),
        source_type="connector",
        content_type=content_type,
        skip_chunking=True,
        synthesis_depth=0,
        extra={
            "recording_duration_seconds": transcription.get("duration_seconds"),
            "scribe_id": transcription_id,
        },
    ))

def _detect_content_type(transcription: dict) -> str:
    """Use recording_type as the authoritative signal -- no speaker count heuristic."""
    mapping = {"meeting": "meeting_transcript", "recording": "1on1_transcript"}
    return mapping.get(transcription.get("recording_type", ""), "meeting_transcript")

def _chunk_by_segments(transcription: dict, max_tokens: int = 400) -> list[str]:
    """Cluster 3-5 consecutive Whisper segments per chunk (time-based, no speaker labels).
    Falls back to paragraph splitting when segments_json is absent."""
    if transcription.get("segments_json"):
        return _cluster_segments(transcription["segments_json"], max_tokens)
    return _split_paragraphs(transcription["text"], max_tokens)
```

### New: `adapters/crawler.py` -- web crawler adapter

```python
# knowledge_ingest/adapters/crawler.py

from knowledge_ingest.ingest import ingest_document
from knowledge_ingest.models import IngestRequest

async def run_crawl_job(
    job_id: str,
    org_id: str,
    kb_slug: str,
    start_url: str,
    max_depth: int = 2,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    rate_limit: float = 2.0,  # requests per second
) -> None:
    """
    Crawl a website and ingest each page into the knowledge pipeline.

    Uses crawl4ai for async crawling. Per page:
    1. Fetch page, respect robots.txt
    2. Detect content type from HTTP headers (text/html vs application/pdf)
    3. Extract clean text (HTML: remove nav/footer/ads; PDF: pypdf extraction)
    4. For PDFs: extract front matter (title + TOC) as metadata
    5. Call ingest_document per page
    6. Update crawl_jobs progress
    """
    ...

async def _ingest_html_page(
    org_id: str, kb_slug: str, url: str, text: str, title: str,
) -> str:
    return await ingest_document(IngestRequest(
        org_id=org_id,
        kb_slug=kb_slug,
        path=url,
        content=text,
        title=title,
        source_type="connector",
        content_type="kb_article",
        synthesis_depth=1,
        extra={"source_url": url, "crawled_at": int(time.time())},
    ))

async def _ingest_pdf_page(
    org_id: str, kb_slug: str, url: str, text: str, title: str, front_matter: str,
) -> str:
    return await ingest_document(IngestRequest(
        org_id=org_id,
        kb_slug=kb_slug,
        path=url,
        content=text,
        title=title,
        source_type="connector",
        content_type="pdf_document",
        synthesis_depth=1,
        extra={
            "source_url": url,
            "crawled_at": int(time.time()),
            "front_matter": front_matter,
        },
    ))
```

### New: API endpoint `POST /knowledge/v1/crawl`

```python
# In routes/knowledge.py (or new routes/crawl.py)

@router.post("/knowledge/v1/crawl")
async def start_crawl(req: CrawlRequest) -> CrawlResponse:
    """Enqueue a web crawl job. Returns immediately with job ID."""
    job_id = str(uuid.uuid4())
    now = int(time.time())
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO knowledge.crawl_jobs
           (id, org_id, kb_slug, config, status, created_at, updated_at)
           VALUES ($1, $2, $3, $4, 'pending', $5, $5)""",
        job_id, req.org_id, req.kb_slug, json.dumps(req.model_dump()), now, now,
    )
    await crawl_tasks.run_crawl.defer_async(
        job_id=job_id,
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        start_url=req.start_url,
        max_depth=req.max_depth,
        include_patterns=req.include_patterns,
        exclude_patterns=req.exclude_patterns,
        rate_limit=req.rate_limit,
    )
    return CrawlResponse(job_id=job_id, status="pending")
```

### New: `sparse_embedder.py` -- FlagEmbedding sidecar client

Thin async HTTP client that calls the FlagEmbedding sidecar and returns a Qdrant `SparseVector`:

```python
# knowledge_ingest/sparse_embedder.py

async def embed_sparse(text: str) -> SparseVector:
    """Call FlagEmbedding sidecar, return Qdrant SparseVector."""
```

At ingest time, `sparse_embedder.embed_sparse(enriched_text)` is called alongside the existing dense embed call. Both vectors are upserted together in the same Qdrant point.

If the sidecar is unreachable, ingest falls back to dense-only with a `WARNING` log entry (`sparse_sidecar_unavailable`, artifact_id). The ingest operation does not fail.

### New Docker service: `bge-m3-sparse`

Added to `deploy/docker-compose.yml`: a FlagEmbedding FastAPI sidecar on port 8001, internal network only, sharing the `tei-models` volume for model weights. The service name is `bge-m3-sparse`.

### Updated: `qdrant_store.py` -- temporal and filter fields in payload

Every `upsert` call must include `content_type`, `valid_from`, `valid_until`, and `ingested_at` in the Qdrant point payload, sourced from the artifact metadata passed through the ingest pipeline (see D15).

### Updated: `qdrant_store.py` -- sparse vector upsert

The `upsert_chunk` function gains a `sparse_vector: SparseVector | None` parameter. When provided, the sparse vector is written to the `vector_sparse` named vector on the Qdrant point. The Qdrant collection schema is updated to include the `vector_sparse` sparse vector config (see D12).

### New: `crawl_tasks.py` -- Procrastinate task for crawling

```python
# knowledge_ingest/crawl_tasks.py
import procrastinate
from knowledge_ingest.enrichment_tasks import app  # reuse same Procrastinate app

@app.task(queue="enrich-bulk", retry=1)
async def run_crawl(
    job_id: str,
    org_id: str,
    kb_slug: str,
    start_url: str,
    max_depth: int,
    include_patterns: list[str] | None,
    exclude_patterns: list[str] | None,
    rate_limit: float,
) -> None:
    from knowledge_ingest.adapters.crawler import run_crawl_job
    await run_crawl_job(
        job_id=job_id, org_id=org_id, kb_slug=kb_slug,
        start_url=start_url, max_depth=max_depth,
        include_patterns=include_patterns, exclude_patterns=exclude_patterns,
        rate_limit=rate_limit,
    )
```

### D16: Three-vector RRF fusion at retrieval time

With three named vectors per Qdrant point (`vector_chunk`, `vector_sparse`, `vector_questions`), retrieval uses three parallel prefetch legs fused via RRF:

```python
results = client.query_points(
    collection_name="klai_knowledge_v2",
    prefetch=[
        Prefetch(query=dense_vec,   using="vector_chunk",     limit=20),
        Prefetch(query=sparse_vec,  using="vector_sparse",    limit=20),
        Prefetch(query=dense_vec,   using="vector_questions", limit=20),
    ],
    query=FusionQuery(fusion=Fusion.RRF),
    limit=top_k,
    query_filter=qdrant_filter,
)
```

Key properties of this approach:

- **RRF over N legs is parameter-free**: `score = Σ 1/(60 + rank_i)` across all legs. No shared score scale needed between dense, sparse, and question vectors.
- **`vector_questions` self-degrades gracefully**: points without this vector are automatically excluded from the questions HNSW graph. For a corpus where only depth 0-1 chunks have `vector_questions` populated (e.g. 20% of all chunks), that leg naturally carries less weight via rank distribution -- no manual weight tuning needed.
- **Query-time sparse vector**: at retrieval time, the same FlagEmbedding sidecar (`POST /embed_sparse`) is called for the user query to produce the sparse query vector. Same model, same weights as at index time. No separate query/document model distinction for BGE-M3 sparse.
- **Evolution path**: start with plain RRF (no training data needed). Switch to convex combination (weighted sum of normalized scores) once sufficient query evaluation data exists. Bevinding 14 in `knowledge-system-fundamentals.md` documents this progression.

**Changes to `retrieve.py`:**
1. Add call to sparse sidecar for query sparse vector alongside existing `embedder.embed_one()` call
2. Replace current `qdrant_store.search()` with three-leg `query_points()` call
3. Reranker pipeline (from KB-005) runs after RRF fusion, unchanged

### D17: Knowledge ingestion is triggered by explicit human action, never automatic

Adding a transcript or meeting to the organizational knowledge layer is a deliberate decision, not an automatic side-effect of recording or summarizing. Users must explicitly choose:
1. Which knowledge base the content should be added to
2. When they want to add it (a completed transcript may not always be worth storing)

Two trigger mechanisms are in scope for KB-006:

**Option A -- Manual action per item**: A "Add to Knowledge" button on the meeting detail page and on the Scribe transcript detail page. The user selects the target KB and confirms. This calls a new portal-api endpoint `POST /api/bots/{meeting_id}/ingest` (for meetings) or `POST /v1/transcriptions/{id}/ingest` (for Scribe), which calls the respective adapter.

**Option B -- Org-level default setting**: An org setting `auto_ingest_meetings: bool` and `auto_ingest_scribe: bool` with a default KB slug. When enabled, completed meetings/transcripts are automatically queued for ingestion to the default KB. This is an opt-in per org, not a system default.

Both options call the same adapter code. The trigger mechanism lives in the portal-api, not in knowledge-ingest.

For KB-006 implementation scope: implement **Option A only** (manual action per item). Option B can be added in a later SPEC once the manual flow is validated.

---

## What is NOT in scope

| Item | Why not now |
|---|---|
| Speaker diarization for Scribe (audio uploads) | Whisper does not diarize; diarization for Scribe recordings is a future concern requiring pyannote.audio or equivalent |
| Pronoun resolution in adapter | Handled by the enrichment LLM prompt (D11); not an adapter concern |
| Auto-detection of content type for unknown sources | Future SPEC; requires classifier and training data |
| Calendar integration for meeting metadata | Future SPEC; enriches participant info but not required for MVP |
| Incremental crawler (re-crawl only changed pages) | Future SPEC; requires ETag/Last-Modified tracking and diff logic |
| Email thread adapter | Future SPEC; profiles and context strategy are defined here but no adapter is built |
| Crawl job management UI (pause, resume, cancel) | Future; the status table exists for API consumption but no UI |

---

## Acceptance criteria

| # | Criterion | EARS pattern |
|---|---|---|
| AC-1 | **When** a document is ingested with `content_type = "meeting_transcript"`, **then** the enrichment task uses the `rolling_window` context strategy and always populates `vector_questions` regardless of `synthesis_depth` | Event-driven |
| AC-2 | **When** a document is ingested with `content_type = "kb_article"` and `synthesis_depth <= 1`, **then** `vector_questions` is populated. **When** `synthesis_depth > 1`, **then** `vector_questions` is NOT populated. | Event-driven |
| AC-3 | **When** a document is ingested with `content_type = "unknown"`, **then** `vector_questions` is never populated and the `first_n` context strategy is used | Event-driven |
| AC-4 | The `knowledge.artifacts` table **shall** have a `content_type TEXT NOT NULL DEFAULT 'unknown'` column and an `extra JSONB NOT NULL DEFAULT '{}'` column after migration 005 | Ubiquitous |
| AC-5 | **When** a meeting is ingested via the meeting adapter (Vexa), **then** the artifact's `content_type` is always `meeting_transcript` | Event-driven |
| AC-6 | **When** a Scribe transcript with `recording_type = "meeting"` is ingested via the Scribe adapter, **then** `content_type = "meeting_transcript"`. **When** `recording_type = "recording"`, **then** `content_type = "1on1_transcript"` | Event-driven |
| AC-7 | **When** a meeting is ingested via the meeting adapter, **then** chunks are created by speaker-turn clusters (3-5 turns), NOT by fixed-token splitting | Event-driven |
| AC-8 | **When** a meeting is ingested via the meeting adapter, **then** participant metadata is stored in `knowledge.artifacts.extra` as `{"participants": [...], "platform": "...", "meeting_title": "..."}` and propagated to the Qdrant payload | Event-driven |
| AC-9 | **When** `POST /knowledge/v1/crawl` is called with a valid configuration, **then** a Procrastinate job is enqueued on `enrich-bulk` and the endpoint returns immediately with a job ID and `status: "pending"` | Event-driven |
| AC-10 | **When** the crawler processes an HTML page, **then** the artifact's `content_type` is `kb_article` and `synthesis_depth` is `1` | Event-driven |
| AC-11 | **When** the crawler encounters a PDF (detected via Content-Type header), **then** the artifact's `content_type` is `pdf_document`, text is extracted via pypdf/pdfminer, and front matter (title + TOC) is stored in `extra` | Event-driven |
| AC-12 | The crawler **shall** respect `robots.txt` and limit requests to the configured rate (default: 2 requests/second) | Ubiquitous |
| AC-13 | **When** the crawler encounters an unreachable page or extraction error, **then** that page is skipped, a warning is logged (job_id, URL, error), and the crawl continues with remaining pages | Unwanted behavior |
| AC-14 | The crawler **shall** update `knowledge.crawl_jobs` with `pages_done` progress as each page is processed, and set `status = 'completed'` or `status = 'failed'` when finished | Ubiquitous |
| AC-15 | **When** `content_type` is `meeting_transcript` or `1on1_transcript`, **then** the enrichment prompt includes participant metadata from `extra.participants` with the instruction to resolve pronouns | Event-driven |
| AC-16 | The `content_profiles.py` module **shall** define profiles for all six content types (`kb_article`, `meeting_transcript`, `1on1_transcript`, `email_thread`, `pdf_document`, `unknown`) with the parameter ranges specified in D4 | Ubiquitous |
| AC-17 | Existing tests pass; no regression on ingest, retrieve, or enrichment endpoints when `content_type = "unknown"` (backward compatibility) | Ubiquitous |
| AC-18 | **When** a chunk is ingested, **then** a `vector_sparse` sparse named vector is computed via the FlagEmbedding sidecar and stored on the Qdrant point alongside `vector_chunk` | Event-driven |
| AC-19 | **When** the sparse sidecar is unreachable, **then** ingest falls back to dense-only with a warning log -- ingest does not fail | Unwanted behavior |
| AC-20 | **When** a chunk is upserted to Qdrant, **then** `content_type`, `valid_from`, `valid_until`, and `ingested_at` are present in the Qdrant point payload | Event-driven |
| AC-21 | **When** a retrieval query includes a `content_type` filter, **then** only chunks with matching `content_type` are returned | Event-driven |
| AC-22 | **When** a meeting is summarized, **then** `decisions` contains objects with `decision`, `rationale`, and `decided_by` fields (nulls allowed) | Event-driven |
| AC-23 | **When** a meeting is summarized, **then** `action_items` contains a `deadline` field (null if not mentioned) | Event-driven |
| AC-24 | **When** a meeting is summarized, **then** `commitments` and `key_quotes` arrays are present (may be empty) | Event-driven |
| AC-25 | **When** `segments_json` is present on a scribe transcription, **then** the Scribe knowledge adapter uses segment boundaries for chunking instead of paragraph splitting | Event-driven |
| AC-26 | The whisper-server response **shall** include a `segments` field containing per-segment `start`, `end`, and `text` alongside the top-level `text` field | Ubiquitous |
| AC-27 | The `scribe.transcriptions` table **shall** have a nullable `segments_json` column for storing whisper segment data; existing rows without segment data are unaffected | Ubiquitous |
| AC-28 | **When** a query is executed, **then** retrieval uses three-leg Qdrant prefetch (`vector_chunk` + `vector_sparse` + `vector_questions`) fused via RRF in a single round-trip | Event-driven |
| AC-29 | **When** a chunk has no `vector_questions`, **then** it is automatically excluded from the questions prefetch leg without application-level filtering, and participates via the other two legs | Unwanted behavior |

---

## Validation approach

### Meeting adapter validation (Vexa)

1. **Unit test with fixture meeting**: Create a test fixture with a VexaMeeting transcript (5 speakers, 45-minute call, diarized segments with `{speaker, text, start, end}`). Verify speaker-turn chunking (3-5 turns per chunk), content_type always set to `meeting_transcript`, and participant metadata extraction.

2. **Integration test**: Send a fixture meeting through the full pipeline (adapter --> ingest --> enrichment --> Qdrant). Verify that enriched chunks use `rolling_window` context strategy and that `vector_questions` is populated.

3. **Participant metadata**: Verify that `extra.participants`, `extra.platform`, and `extra.meeting_title` are stored and propagated to the Qdrant payload.

### Scribe adapter validation

1. **Unit test with fixture transcription**: Create test fixtures with `recording_type = "meeting"` and `recording_type = "recording"`. Verify correct `content_type` assignment for each. Verify segment-cluster chunking when `segments_json` is present and paragraph-split fallback when absent.

2. **Integration test**: Send a fixture transcription through the full pipeline (adapter --> ingest --> enrichment --> Qdrant). Verify that `content_type = "meeting_transcript"` transcriptions use `rolling_window` context strategy and that `vector_questions` is populated.

3. **recording_type fallback**: Verify that a transcription with missing or unknown `recording_type` defaults gracefully (does not raise, assigns a valid content_type).

### Crawler adapter validation

1. **Unit test with mock server**: Stand up a local HTTP server with 5 HTML pages and 1 PDF. Run the crawler. Verify page count, content_type detection (HTML vs PDF), rate limiting (measure time between requests), and `robots.txt` respect (block one page, verify it is skipped).

2. **Integration test**: Crawl a small test site (5 pages). Verify artifacts in PostgreSQL with correct `content_type`, `extra.source_url`, and `synthesis_depth = 1`. Verify Qdrant points with enriched vectors.

3. **Error resilience**: Include a 404 page and a timeout page in the test server. Verify the crawler skips them, logs warnings, and completes the remaining pages.

### Profile system validation

1. **Profile lookup test**: For each of the six content types, verify that `get_profile()` returns the correct profile with expected parameter ranges.

2. **HyPE conditional test**: For `kb_article` with `synthesis_depth=0`, verify HyPE is enabled. For `kb_article` with `synthesis_depth=3`, verify HyPE is disabled. For `meeting_transcript` with any depth, verify HyPE is always enabled.

3. **Context strategy test**: For a transcript, verify that `rolling_window` extracts preceding turns (not first-N tokens). For a PDF, verify that `front_matter` uses title+TOC when available and falls back to first-N when not.

### Retrieval quality comparison

Reuse the KB-005 validation approach (test set of 50-100 real queries) with the addition of transcript queries:

1. Ingest 5-10 meeting transcripts through the Scribe adapter
2. Create 20 test queries about meeting decisions, action items, and participants
3. Compare Recall@5 and MRR@5 between: (a) raw embedding without profiles, (b) profile-based enrichment with rolling window + HyPE
4. Target: transcript queries should show >15% Recall@5 improvement with profile-based enrichment vs. the KB-005 first-N approach

---

## Implementation notes (2026-03-26)

PR #32 (`feat/kb-006-content-type-adapters`) was merged on 2026-03-26. Implementation is complete and matches the SPEC with a few noteworthy deviations and additions.

### What was built vs. what was specced

**Matches SPEC:**
- `content_profiles.py` — all 6 `ContentTypeProfile` entries with the exact HYPE, context strategy, and chunk-token parameters from this SPEC
- `context_strategies.py` — all 4 named strategy functions (`first_n`, `rolling_window`, `most_recent`, `front_matter`)
- `sparse_embedder.py` — async HTTP client to the BGE-M3 FlagEmbedding sidecar; dense-only fallback on sidecar unavailability
- DB migrations 005 (`content_type`, `extra` columns on `knowledge.artifacts`) and 006 (`knowledge.crawl_jobs` table)
- Scribe adapter (`klai-scribe/scribe-api/app/services/knowledge_adapter.py`) — `recording_type`-based content_type detection, segment clustering, paragraph-split fallback
- Portal meeting adapter (`klai-portal/backend/app/services/knowledge_adapter.py`) — Vexa speaker-turn clustering, participant metadata, `POST /api/bots/{meeting_id}/ingest` trigger endpoint
- Crawler adapter (`adapters/crawler.py`) using `crawl4ai`, with `crawl_tasks.py` Procrastinate integration and `POST /knowledge/v1/crawl` endpoint
- Qdrant `upsert` updated for temporal payload fields (`content_type`, `valid_from`, `valid_until`, `ingested_at`) and sparse named vector
- Whisper-server updated to return `segments` array alongside `text` (D13)
- Alembic migration `0005_add_segments_and_recording_type.py` for `scribe.transcriptions`
- BGE-M3 sparse sidecar service added to `deploy/bge-m3-sparse/` and `docker-compose.yml`
- 52 unit tests; ruff clean

**Deviation: meeting summary schema (D14/AC-22–24)**

The enriched meeting extraction schema (D14 — `commitments`, `key_quotes`, enriched `decisions`, `deadline` on action items) was partially implemented in `summarizer.py`. The structured extraction prompt was updated but `commitments` and `key_quotes` were added as optional fields rather than always-present arrays per AC-24. Frontend backward-compat handling was deferred: the frontend summary renderer still uses the old schema shape. This is noted as a follow-up in `klai-portal/backend` — a separate small PR is expected before the staging deploy.

**Deviation: three-leg RRF retrieval (D16/AC-28–29)**

The Qdrant collection schema and ingest-time sparse upsert were fully implemented. The retrieval side (`retrieve.py`) was updated to call the sparse sidecar for query sparse vectors, but the three-leg prefetch + RRF fusion (D16) was not merged in this PR. The current retrieval still uses the two-leg approach from KB-005 (`vector_chunk` + `vector_questions`). The sparse leg (`vector_sparse`) is indexed and populated but not yet queried. This is intentional: D16 retrieval will be shipped once the sparse index is populated on staging and retrieval quality can be evaluated. Tracked as a follow-up SPEC or addendum to KB-005/KB-006.

**Deviation: Qdrant collection renamed**

The collection was renamed from `klai_kb` (legacy) to `klai_knowledge` (per the SPEC's `klai_knowledge_v2` intent, simplified to `klai_knowledge`). All ingest and retrieve code was updated atomically. The `_v2` suffix from the SPEC code examples was dropped — not needed given the clean rename.

**Addition: whisper-server language detection pass-through**

Beyond what was specced (adding `segments` to the Whisper response), the whisper-server was also updated to pass through the detected language from faster-whisper to the Scribe API response. This was a trivial addition discovered during the implementation of D13 and was included in the same PR.

### Key decisions made during implementation

1. **Scribe adapter lives in `scribe-api`, not `knowledge-ingest`**: The adapter that reads from `scribe.transcriptions` was placed in `klai-scribe/scribe-api/app/services/knowledge_adapter.py` rather than in `knowledge-ingest`. This keeps the data access boundary clean — knowledge-ingest has no DB dependency on the scribe schema. The adapter fetches data internally and calls the knowledge-ingest HTTP endpoint.

2. **Portal meeting adapter follows the same pattern**: `klai-portal/backend/app/services/knowledge_adapter.py` — portal owns the Vexa meeting data and calls knowledge-ingest over HTTP. No cross-service DB access.

3. **`crawl4ai` dependency pinned**: `crawl4ai` was added to `knowledge-ingest` requirements. It brought in several async dependencies that were already in the image; no Dockerfile changes required beyond the `requirements.txt` pin.

4. **BGE-M3 sidecar uses CPU by default**: The `deploy/bge-m3-sparse/Dockerfile` targets CPU inference. The sidecar is separated from the GPU TEI service intentionally — sparse inference at batch ingest rates is feasible on CPU, and this avoids contention on the GPU during enrichment LLM calls.

### Outstanding before staging deploy

- Frontend backward-compat for enriched meeting summary schema (D14)
- Three-leg RRF retrieval (D16) — separate PR once sparse index is populated
- Staging smoke test: ingest via crawl job + verify Qdrant sparse vector population
