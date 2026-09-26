"""Name the tenant, and the feature, a master-key LiteLLM call is made for.

portal-api calls LiteLLM with the master key, which belongs to no tenant, so
LiteLLM's PII enforcer (``deploy/litellm/klai_pii_enforce.py``) cannot tell
whose text a call carries and masks nothing. The enforcer accepts the tenant
from ``metadata._klai_delegated_org_id``, and only on the master key, so a
tenant key cannot pick another org's policy this way.
"""

from typing import Any


def with_delegated_org(body: dict[str, Any], zitadel_org_id: str | None) -> dict[str, Any]:
    """``body`` with the tenant's Zitadel org id added to its metadata; unchanged without one.

    Added, not replaced: a passthrough call also carries ``_klai_openai_passthrough``,
    which tells the LiteLLM knowledge hook to leave the call alone.
    """
    if zitadel_org_id:
        body["metadata"] = {**(body.get("metadata") or {}), "_klai_delegated_org_id": zitadel_org_id}
    return body


def with_feature_tag(body: dict[str, Any], tag: str) -> dict[str, Any]:
    """``body`` with ``tag`` recorded as a LiteLLM spend tag (``metadata.tags``).

    ``LiteLLM_SpendLogs.request_tags`` is read from this field
    (``get_logging_payload`` in litellm's ``proxy/spend_tracking/spend_tracking_utils.py``),
    verified against the litellm 1.96.2 actually running on core-01
    (klai-core-litellm-1) by reading the installed source. The same payload
    builder runs on the success and the failure logging path, so a tag
    survives a 402/429 spend row exactly like a 200 one. Added, not replaced,
    same as ``with_delegated_org`` above.
    """
    metadata = body.get("metadata") or {}
    body["metadata"] = {**metadata, "tags": [*(metadata.get("tags") or []), tag]}
    return body
