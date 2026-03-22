from pydantic import BaseModel


class IngestRequest(BaseModel):
    org_id: str          # Zitadel org ID (used as Qdrant tenant scope)
    kb_slug: str         # e.g. "personal"
    path: str            # e.g. "my-note.md" (relative within KB)
    content: str         # Full markdown content (with optional frontmatter)
    user_id: str | None = None  # Set for user-scoped personal KB


class RetrieveRequest(BaseModel):
    org_id: str
    query: str
    top_k: int = 5
    kb_slugs: list[str] | None = None  # None = all org KBs


class ChunkResult(BaseModel):
    text: str
    source: str          # "{kb_slug}/{path}"
    score: float
    metadata: dict = {}


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
