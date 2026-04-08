from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RetrieveRequest(BaseModel):
    query: str
    org_id: str
    scope: Literal["personal", "org", "both", "notebook", "broad"] = "org"
    user_id: str | None = None
    notebook_id: str | None = None
    top_k: int = 8
    conversation_history: list[dict] = Field(default_factory=list)
    kb_slugs: list[str] | None = None
    taxonomy_node_ids: list[int] | None = None  # SPEC-KB-022 R3: optional taxonomy filter
    tags: list[str] | None = None  # SPEC-KB-022 R3: optional tag filter


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
