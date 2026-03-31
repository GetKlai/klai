from typing import Literal, get_args

from pydantic import BaseModel, Field

AssertionMode = Literal["factual", "belief", "hypothesis", "procedural", "quoted", "unknown"]
VALID_ASSERTION_MODES: frozenset[str] = frozenset(get_args(AssertionMode))


class IngestRequest(BaseModel):
    org_id: str          # Zitadel org ID (used as Qdrant tenant scope)
    kb_slug: str         # e.g. "personal"
    path: str            # e.g. "my-note.md" (relative within KB)
    content: str = Field(max_length=500_000)  # Full markdown content (with optional frontmatter)
    user_id: str | None = None  # Set for user-scoped personal KB
    source_type: str | None = None  # e.g. "docs", "connector", "crawl"
    content_type: str = "unknown"  # e.g. "kb_article", "meeting_transcript", "pdf_document"
    skip_chunking: bool = False  # True when adapter provides pre-chunked text
    extra: dict = {}  # Adapter-specific metadata (participants, source_url, etc.)
    chunks: list[str] | None = None  # Pre-computed chunks (used with skip_chunking=True)
    synthesis_depth: int | None = None  # Optional override (adapters set this explicitly)
    allowed_assertion_modes: list[str] | None = None  # Connector-level hint: expected modes for this source


class RetrieveRequest(BaseModel):
    org_id: str
    query: str = Field(max_length=2_000)
    top_k: int = Field(default=5, ge=1, le=50)
    kb_slugs: list[str] | None = None  # None = all org KBs
    user_id: str | None = None  # For personal-scope filtering
    sparse_weight: float | None = None  # AC-7: reserved for weighted RRF; None = pure RRF


class ChunkResult(BaseModel):
    text: str
    source: str          # "{kb_slug}/{path}"
    score: float
    metadata: dict = {}
    artifact_id: str | None = None
    provenance_type: str | None = None
    assertion_mode: str | None = None
    synthesis_depth: int | None = None
    confidence: str | None = None


class RetrieveResponse(BaseModel):
    chunks: list[ChunkResult]


class CrawlRequest(BaseModel):
    org_id: str
    kb_slug: str
    url: str                          # URL to fetch and ingest
    path: str | None = None           # Override storage path (default: derived from URL)


class CrawlResponse(BaseModel):
    url: str
    path: str
    chunks_ingested: int


class DeleteKBRequest(BaseModel):
    org_id: str
    kb_slug: str


class UpdateKBVisibilityRequest(BaseModel):
    org_id: str
    kb_slug: str
    visibility: str  # "public" | "private"


class ArtifactSummary(BaseModel):
    id: str  # UUID
    path: str
    assertion_mode: str | None = None
    tags: list[str] = []
    created_at: str  # ISO 8601


class PersonalItemsResponse(BaseModel):
    items: list[ArtifactSummary]
    total: int
    limit: int
    offset: int


class BulkCrawlRequest(BaseModel):
    org_id: str
    kb_slug: str
    start_url: str
    max_depth: int = 2
    include_patterns: list[str] | None = None
    exclude_patterns: list[str] | None = None
    rate_limit: float = 2.0
    content_selector: str | None = None


class BulkCrawlResponse(BaseModel):
    job_id: str
    status: str


class KBWebhookRequest(BaseModel):
    org_id: str
    kb_slug: str
    gitea_repo: str  # e.g. "org-myslug/personal"


class BulkSyncRequest(BaseModel):
    org_id: str
    kb_slug: str
    gitea_repo: str


class GiteaPusher(BaseModel):
    name: str | None = None
    login: str | None = None


class GiteaCommit(BaseModel):
    added: list[str] = []
    modified: list[str] = []
    removed: list[str] = []


class GiteaRepository(BaseModel):
    full_name: str        # e.g. "org-myslug/personal"


class GiteaPushEvent(BaseModel):
    ref: str              # e.g. "refs/heads/main"
    commits: list[GiteaCommit] = []
    repository: GiteaRepository
    pusher: GiteaPusher | None = None
