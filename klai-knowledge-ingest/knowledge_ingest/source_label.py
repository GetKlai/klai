"""Source label computation for chunk payload (SPEC-KB-021 Change 1)."""
from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from knowledge_ingest.models import IngestRequest


def compute_source_label(req: IngestRequest) -> str:
    """Compute a human-readable source label for a chunk payload.

    Priority order:
    1. crawl + source_domain  → domain (e.g. "help.mitel.nl")
    2. direct URL source + source_ref/source_url → domain
    3. connector + connector_type → connector_type slug
    4. connector + source_connector_id (no connector_type) → connector id
    5. content_type contains "transcript" or "1on1" → "meetings"
    6. fallback → kb_slug
    """
    if req.source_type == "crawl" and req.source_domain:
        return req.source_domain
    if req.source_type == "url":
        source_url = req.source_ref or str((req.extra or {}).get("source_url") or "")
        host = urlparse(source_url).hostname if source_url else None
        if host:
            return host
    if req.source_type == "connector":
        if req.connector_type:
            return req.connector_type
        if req.source_connector_id:
            return req.source_connector_id
    content_type_lower = (req.content_type or "").lower()
    if "transcript" in content_type_lower or "1on1" in content_type_lower:
        return "meetings"
    return req.kb_slug
