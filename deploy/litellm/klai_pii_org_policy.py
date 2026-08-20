"""Per-org entity policy resolution (REQ-7, NFR "Tenant isolation").

REQ-7's optional set (``IBAN_CODE``, ``CREDIT_CARD``, ``EMAIL_ADDRESS``,
``PHONE_NUMBER``, ``NL_KVK``, ``NL_BTW``, ``NL_POSTCODE``) is "per-org,
default off". The NFR says that policy "is resolved through the existing
settings path and cached per org" — this module is that path, following the
same shape as ``klai_kb_portal_client.py``'s ``get_kb_feature`` (a portal-api
GET, Bearer-authenticated with ``PORTAL_INTERNAL_SECRET``, short-TTL cache,
fail CLOSED on any error) without importing or modifying that file.

Honest limitation, stated rather than hidden: portal-api does not yet expose
the endpoint this module calls (``/internal/v1/orgs/{org_id}/pii-entities``).
Standing it up is portal-api backend work — a DB column/table plus an
endpoint plus a migration, explicitly named as PR3 scope in the SPEC's own
Implementation Handoff table — and is out of scope for this change, which
only adds/wires ``deploy/litellm`` modules. Until that endpoint exists, every
call here fails (connection refused / 404) and therefore returns the empty
policy — which is *exactly* REQ-7's own stated default ("per-org, default
off"). So the absence of the real endpoint does not create a gap between
what this code does and what the SPEC asks for by default: an org with no
resolvable policy gets no optional entities enabled, same as an org that
explicitly has none configured. Only the "an operator turns IBAN_CODE on for
one org" path needs the real endpoint to exist, and `KLAI_PII_ENFORCE` is off
by default regardless (this whole module is unreachable in production until
that flag flips for at least one deployment).

Caching here is a small in-process TTL dict, not Redis. ``get_kb_feature``
uses Redis so a shared cache survives across LiteLLM worker processes and
portal-api can push invalidations; neither concern applies yet to a policy
this module is the only caller of and portal-api cannot yet write. A
Redis-backed cache mirroring ``get_kb_feature`` is a reasonable follow-up
once the real endpoint exists — this is a deliberate scope decision, not an
oversight.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from klai_pii_entities import RETURN_SET_ENTITIES

logger = logging.getLogger(__name__)

PORTAL_API_URL = os.getenv("PORTAL_API_URL", "http://portal-api:8000")
PORTAL_INTERNAL_SECRET = os.getenv("PORTAL_INTERNAL_SECRET", "")

_POLICY_TIMEOUT_SECONDS = float(os.getenv("KLAI_PII_POLICY_TIMEOUT_SECONDS", "2.0"))
_POLICY_CACHE_TTL_SECONDS = float(os.getenv("KLAI_PII_POLICY_CACHE_TTL_SECONDS", "30"))

# {org_id: (fetched_at_monotonic, frozenset[str])}
_policy_cache: dict[Any, tuple[float, frozenset[str]]] = {}

EMPTY_POLICY: frozenset[str] = frozenset()


def _cache_get(org_id: Any) -> frozenset[str] | None:
    cached = _policy_cache.get(org_id)
    if cached is None:
        return None
    fetched_at, policy = cached
    if time.monotonic() - fetched_at > _POLICY_CACHE_TTL_SECONDS:
        return None
    return policy


def _cache_set(org_id: Any, policy: frozenset[str]) -> None:
    _policy_cache[org_id] = (time.monotonic(), policy)


def clear_policy_cache() -> None:
    """Test hook — no production caller. See tests/klai_module_reset.py."""
    _policy_cache.clear()


async def resolve_org_entity_policy(org_id: Any) -> frozenset[str]:
    """Return the RETURN_SET entities this org has opted into.

    Fails CLOSED (empty set) on: missing ``org_id`` (master-key / widget
    calls, same shape ``klai_pii_observe.py`` names as a KlaiKnowledgeHook
    blind spot — here there is simply no org to look a policy up for, so
    none of the optional entities can be enabled), no
    ``PORTAL_INTERNAL_SECRET`` configured, a network error, a timeout, or a
    non-2xx response. None of these raise — an unresolvable policy is not
    the same failure class as REQ-10's analyzer-unreachable case (that one
    fails the request); this one just means "no optional entities for this
    request", which is REQ-7's own default.

    Only entity types that are actual members of ``RETURN_SET_ENTITIES``
    survive the response parse — an unrecognised key (typo, future entity
    the deployed pack does not implement yet, or literally ``"PERSON"``) is
    silently dropped rather than trusted. This is the second half of
    PERSON's structural exclusion (the first half is
    ``klai_pii_entities.effective_enabled_entities``'s set intersection):
    even a directly-crafted portal-api response claiming PERSON is enabled
    cannot make it out of this function.
    """
    if not org_id:
        return EMPTY_POLICY

    cached = _cache_get(org_id)
    if cached is not None:
        return cached

    if not PORTAL_INTERNAL_SECRET:
        logger.warning(
            "pii_org_policy_no_secret org_id=%s — PORTAL_INTERNAL_SECRET not set, "
            "fail-closed to no optional entities",
            org_id,
        )
        return EMPTY_POLICY

    try:
        async with httpx.AsyncClient(timeout=_POLICY_TIMEOUT_SECONDS) as http:
            response = await http.get(
                f"{PORTAL_API_URL}/internal/v1/orgs/{org_id}/pii-entities",
                headers={"Authorization": f"Bearer {PORTAL_INTERNAL_SECRET}"},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning(
            "pii_org_policy_fetch_failed org_id=%s error=%s — fail-closed to no "
            "optional entities",
            org_id,
            exc,
        )
        return EMPTY_POLICY

    enabled = payload.get("enabled_entities") if isinstance(payload, dict) else None
    if not isinstance(enabled, list):
        return EMPTY_POLICY

    policy = frozenset(e for e in enabled if isinstance(e, str)) & RETURN_SET_ENTITIES
    _cache_set(org_id, policy)
    return policy
