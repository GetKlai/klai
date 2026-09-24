"""Name the tenant a master-key LiteLLM call is made for.

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
