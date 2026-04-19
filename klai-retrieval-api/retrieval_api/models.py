from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# SPEC-SEC-010 REQ-2.5: maximum allowed length of a single conversation_history entry's
# content field. Longer strings are rejected with HTTP 422 at the Pydantic layer.
_CONVERSATION_CONTENT_MAX_CHARS = 8_000


class RetrieveRequest(BaseModel):
    query: str
    org_id: str
    scope: Literal["personal", "org", "both", "notebook", "broad"] = "org"
    user_id: str | None = None
    notebook_id: str | None = None
    # SPEC-SEC-010 REQ-2.1: top_k bounded to [1, 50] to block abusive payloads (F-010).
    top_k: int = Field(8, ge=1, le=50)
    # SPEC-SEC-010 REQ-2.2: conversation_history length bounded to 20 entries.
    conversation_history: list[dict] = Field(default_factory=list, max_length=20)
    # SPEC-SEC-010 REQ-2.3: kb_slugs list length bounded to 20 entries.
    kb_slugs: list[str] | None = Field(None, max_length=20)
    # SPEC-SEC-010 REQ-2.4: taxonomy_node_ids list length bounded to 50 entries.
    taxonomy_node_ids: list[int] | None = Field(None, max_length=50)
    # SPEC-SEC-010 REQ-2.3 (tags parity): tags list length bounded to 20 entries.
    tags: list[str] | None = Field(None, max_length=20)

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
    score: float
    reranker_score: float | None = None
    scope: str | None = None
    valid_at: str | None = None
    invalid_at: str | None = None
    ingested_at: int | None = None
    assertion_mode: str | None = None
    final_score: float | None = None
    evidence_tier_metadata: dict | None = None
    source_ref: str | None = None          # Notion page UUID, URL, or repo path
    source_connector_id: str | None = None  # Connector that produced this chunk
    source_url: str | None = None           # Canonical URL for this source
    kb_slug: str | None = None             # Knowledge base slug (SPEC-KB-021)
    source_label: str | None = None        # Human-readable source label (SPEC-KB-021)
    title: str | None = None               # Document title from Qdrant payload
    image_urls: list[str] | None = None    # Presigned S3 URLs for images in this document


class RetrieveMetadata(BaseModel):
    candidates_retrieved: int
    reranked_to: int
    retrieval_ms: float
    rerank_ms: float | None = None
    gate_margin: float | None = None
    graph_results_count: int = 0
    graph_search_ms: float | None = None


class RetrieveResponse(BaseModel):
    query_resolved: str
    retrieval_bypassed: bool
    chunks: list[ChunkResult]
    metadata: RetrieveMetadata


class Citation(BaseModel):
    index: int
    artifact_id: str | None = None
    title: str
    chunk_ids: list[str]
    relevance_score: float
