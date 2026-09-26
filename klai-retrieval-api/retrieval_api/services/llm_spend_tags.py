"""Tag a LiteLLM chat-completions body with the retrieval-api feature that sent it.

Mirrors ``klai-portal/backend/app/services/litellm_delegation.with_feature_tag``:
written to ``metadata.tags``, the field ``LiteLLM_SpendLogs.request_tags`` is
read from (``get_logging_payload`` in litellm's
``proxy/spend_tracking/spend_tracking_utils.py``), verified against the
litellm 1.96.2 running on core-01 (klai-core-litellm-1) by reading the
installed source. Duplicated rather than imported: retrieval-api and
portal-api are separate deployables with no shared runtime package for this.
"""

from __future__ import annotations

from typing import Any


def with_feature_tag(body: dict[str, Any], tag: str) -> dict[str, Any]:
    """``body`` with ``tag`` added to its LiteLLM spend tags. Added, not replaced."""
    metadata = body.get("metadata") or {}
    body["metadata"] = {**metadata, "tags": [*(metadata.get("tags") or []), tag]}
    return body
