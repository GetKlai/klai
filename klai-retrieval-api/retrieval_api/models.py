from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# SPEC-SEC-010 REQ-2.5: maximum allowed length of a single conversation_history entry's
# content field. Longer strings are rejected with HTTP 422 at the Pydantic layer.
_CONVERSATION_CONTENT_MAX_CHARS = 8_000


class PageContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str | None = Field(default=None, max_length=2048)
    path: str | None = Field(default=None, max_length=512)
    title: str | None = Field(default=None, max_length=512)
    referrer: str | None = Field(default=None, max_length=2048)
    excerpt: str | None = Field(default=None, max_length=2000)


class RetrieveRequest(BaseModel):
    query: str
    # Optional original user text when the caller sends a rewritten query for
    # retrieval. Evidence/source matching uses both so a rewrite cannot make
    # otherwise relevant citable chunks look unrelated.
    raw_query: str | None = None
    org_id: str
    scope: Literal["personal", "org", "both"] = "org"
    user_id: str | None = None
    # SPEC-SEC-010 REQ-2.1: top_k bounded to [1, 50] to block abusive payloads (F-010).
    top_k: int = Field(8, ge=1, le=50)
    # SPEC-SEC-010 REQ-2.2: conversation_history length bounded to 20 entries.
    conversation_history: list[dict] = Field(default_factory=list, max_length=20)
    # Optional page metadata from embedded widgets. URL is used only as a weak
    # ranking hint; excerpt remains caller context and is never a hard retrieval scope.
    page_context: PageContext | None = None
    # SPEC-SEC-010 REQ-2.3: kb_slugs list length bounded to 20 entries.
    kb_slugs: list[str] | None = Field(None, max_length=20)
    # Include all private chunks owned by user_id. LiteLLM sets this only for
    # the "all collections" state; explicit subsets keep using kb_slugs.
    include_owned_private_kbs: bool = False
    # Strict/Open answer mode from the chat UI. Retrieval uses this to avoid
    # gate bypasses in Strict mode; generation still decides how to answer.
    kb_narrow: bool = False
    # SPEC-SEC-010 REQ-2.4: taxonomy_node_ids list length bounded to 50 entries.
    taxonomy_node_ids: list[int] | None = Field(None, max_length=50)
    # SPEC-SEC-010 REQ-2.3 (tags parity): tags list length bounded to 20 entries.
    tags: list[str] | None = Field(None, max_length=20)
    # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-3: per-tenant telemetry mode threaded
    # from litellm-hook / knowledge-mcp. Default 'shadow' so older clients
    # without the field continue to work in the privacy-friendly mode (REQ-4
    # fail-open). Validation: gates content emission in REQ-5/6/7/8/9.
    telemetry_level: Literal["off", "shadow", "full"] = "shadow"
    # SPEC-PORTAL-RBAC-REFACTOR-001 REQ-17: effective role propagated from the
    # MCP caller. Defaults to "unknown" so callers without the field (older
    # LiteLLM hook builds) continue to work without change. Retrieval-api uses
    # this to apply personal-role slug filtering (REQ-6).
    effective_role: str = "unknown"

    @field_validator("conversation_history")
    @classmethod
    def _validate_conversation_content_length(cls, history: list[dict]) -> list[dict]:
        """REQ-2.5: reject any conversation_history entry with content > 8 000 chars.

        We do NOT silently truncate (REQ-2.6) — oversized payloads always yield 422.
        """
        for idx, entry in enumerate(history):
            content = entry.get("content") if isinstance(entry, dict) else None
            if isinstance(content, str) and len(content) > _CONVERSATION_CONTENT_MAX_CHARS:
                raise ValueError(
                    f"conversation_history[{idx}].content exceeds "
                    f"{_CONVERSATION_CONTENT_MAX_CHARS} characters"
                )
        return history


class ChunkResult(BaseModel):
    chunk_id: str
    artifact_id: str | None = None
    content_type: str | None = None
    text: str
    context_prefix: str | None = None
    heading_path: str | None = None
    score: float
    reranker_score: float | None = None
    scope: str | None = None
    valid_at: str | None = None
    invalid_at: str | None = None
    ingested_at: int | None = None
    assertion_mode: str | None = None
    final_score: float | None = None
    evidence_tier_metadata: dict | None = None
    source_ref: str | None = None  # Notion page UUID, URL, or repo path
    source_connector_id: str | None = None  # Connector that produced this chunk
    source_url: str | None = None  # Canonical URL for this source
    kb_slug: str | None = None  # Knowledge base slug (SPEC-KB-021)
    source_label: str | None = None  # Human-readable source label (SPEC-KB-021)
    title: str | None = None  # Document title from Qdrant payload
    image_urls: list[str] | None = None  # Presigned S3 URLs for images in this document
    # Specific brand/product entity names extracted by Graphiti at document ingest
    # and filtered per-chunk by literal substring presence in chunk text. Lets the
    # LLM cite "this chunk mentions Bubble Cloud" as evidence and lets clients
    # filter by entity_names server-side. Empty/absent for chunks without coverage.
    entity_names: list[str] | None = None
    # SPEC-RAG-PARENT-CHILD-001: when present, ``text`` already carries the
    # parent's broader-context text (matched on the smaller child chunk).
    # Clients can use this flag for debugging / display hints.
    is_parent_text: bool = False


class EvidenceItem(BaseModel):
    evidence_id: str
    chunk_id: str
    artifact_id: str | None = None
    content_type: str | None = None
    text: str
    title: str | None = None
    heading_path: str | None = None
    source_url: str | None = None
    source_label: str | None = None
    score: float
    reranker_score: float | None = None
    final_score: float | None = None
    scope: str | None = None
    image_urls: list[str] | None = None
    is_parent_text: bool = False


class EvidenceSource(BaseModel):
    source_id: str
    title: str
    source_url: str | None = None
    artifact_id: str | None = None
    source_label: str | None = None
    evidence_ids: list[str]
    relevance_score: float


class EvidencePack(BaseModel):
    items: list[EvidenceItem] = Field(default_factory=list)
    sources: list[EvidenceSource] = Field(default_factory=list)
    no_citable_reason: str | None = None


class RetrieveMetadata(BaseModel):
    candidates_retrieved: int
    reranked_to: int
    retrieval_ms: float
    rerank_ms: float | None = None
    gate_margin: float | None = None
    graph_results_count: int = 0
    graph_search_ms: float | None = None


ConfidenceBand = Literal["high", "medium", "low", "unknown"]


class RetrieveResponse(BaseModel):
    query_resolved: str
    retrieval_bypassed: bool
    chunks: list[ChunkResult]
    metadata: RetrieveMetadata
    # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1: max(reranker_scores) over the
    # served chunks mapped to a band. Consumed by the litellm-hook to decide
    # whether to inject the anti-hallucination instruction (REQ-2).
    # ``None`` only on retrieval-bypass (gate) paths; otherwise always set.
    confidence_band: ConfidenceBand | None = None
    # Deterministic citation contract. Downstream clients should render sources
    # only from this pack; ``chunks`` remains for compatibility/debugging.
    evidence_pack: EvidencePack | None = None


class Citation(BaseModel):
    index: int
    artifact_id: str | None = None
    title: str
    chunk_ids: list[str]
    relevance_score: float
