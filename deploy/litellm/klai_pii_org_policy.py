"""Per-org entity policy resolution (REQ-7, NFR "Tenant isolation").

REQ-7's optional set (``IBAN_CODE``, ``CREDIT_CARD``, ``EMAIL_ADDRESS``,
``PHONE_NUMBER``, ``NL_KVK``, ``NL_BTW``, ``NL_POSTCODE``) is per-org and, since
2026-09-03, on by default for every tenant
(SPEC-PRIVACY-PII-POLICY-ADMIN-001 D2). The NFR says that policy "is resolved
through the existing settings path and cached per org" — this module is that
path, following the same shape as ``klai_kb_portal_client.py``'s
``get_kb_feature`` (a portal-api GET, Bearer-authenticated with
``PORTAL_INTERNAL_SECRET``, short-TTL cache) without importing or modifying that
file. It deliberately does NOT copy that module's fail-closed-on-any-error
behaviour — see ``_degraded`` for what an unreachable portal-api resolves to,
and why that answer had to change when the default flipped to on.

``GET /internal/v1/orgs/{org_id}/pii-entities`` exists (``app/api/internal.py``)
and also carries the tenant's ``telemetry_level``, so this module resolves both
halves in one fetch — see ``OrgPiiContext``.

Caching here is a small in-process TTL dict, not Redis. ``get_kb_feature``
uses Redis so a shared cache survives across LiteLLM worker processes and
portal-api can push invalidations; neither concern applies yet to a policy
this module is the only caller of. A Redis-backed cache mirroring
``get_kb_feature`` is a reasonable follow-up — this is a deliberate scope
decision, not an oversight.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, NamedTuple

import httpx

from klai_pii_entities import RETURN_SET_ENTITIES

logger = logging.getLogger(__name__)


class OrgPiiContext(NamedTuple):
    """Everything the enforcement side needs to know about ONE tenant.

    Two fields, one fetch, one cache entry — deliberately. Splitting the
    telemetry mode onto its own endpoint would give the two answers
    independent cache lifetimes, and a tenant could then be masked under one
    view of its settings while being logged under another.
    """

    entities: frozenset[str]
    telemetry_level: str


PORTAL_API_URL = os.getenv("PORTAL_API_URL", "http://portal-api:8000")
PORTAL_INTERNAL_SECRET = os.getenv("PORTAL_INTERNAL_SECRET", "")

_POLICY_TIMEOUT_SECONDS = float(os.getenv("KLAI_PII_POLICY_TIMEOUT_SECONDS", "2.0"))
_POLICY_CACHE_TTL_SECONDS = float(os.getenv("KLAI_PII_POLICY_CACHE_TTL_SECONDS", "30"))

# {org_id: (fetched_at_monotonic, OrgPiiContext)}
_policy_cache: dict[Any, tuple[float, OrgPiiContext]] = {}

# {org_id: OrgPiiContext} — the last context that RESOLVED, with no TTL.
# Separate from the TTL cache on purpose: the TTL decides when to re-ask,
# this decides what to answer when asking fails. See ``_degraded``.
_last_good: dict[Any, OrgPiiContext] = {}

EMPTY_POLICY: frozenset[str] = frozenset()

# The telemetry mode assumed when we could not establish the tenant's real one.
# ``off``, not ``shadow``: SPEC-PRIVACY-QUERY-SHADOW-001 REQ-4's fail-OPEN
# default to ``shadow`` governs the retrieval hook, which has to keep working
# without telemetry settings. This is a NEW emission path, and the safe
# direction for a new one is silence — a tenant that never authorised
# telemetry must not get it because a fetch timed out or because portal-api
# in a rolling deploy is old enough not to send the field yet.
TELEMETRY_LEVEL_WHEN_UNKNOWN = "off"

EMPTY_CONTEXT = OrgPiiContext(entities=EMPTY_POLICY, telemetry_level=TELEMETRY_LEVEL_WHEN_UNKNOWN)


def _cache_get(org_id: Any) -> OrgPiiContext | None:
    cached = _policy_cache.get(org_id)
    if cached is None:
        return None
    fetched_at, context = cached
    if time.monotonic() - fetched_at > _POLICY_CACHE_TTL_SECONDS:
        return None
    return context


def _cache_set(org_id: Any, context: OrgPiiContext) -> None:
    _policy_cache[org_id] = (time.monotonic(), context)
    _last_good[org_id] = context


def clear_policy_cache() -> None:
    """Test hook — no production caller. See tests/klai_module_reset.py."""
    _policy_cache.clear()
    _last_good.clear()


def _degraded(org_id: Any, reason: str) -> OrgPiiContext:
    """The answer to use when portal-api could not be asked.

    Serve the last context we successfully resolved for this org, if there
    ever was one, rather than ``EMPTY_CONTEXT``.

    This changed meaning on 2026-09-03 and the change matters. Under REQ-7's
    original "per-org, default off", ``EMPTY_CONTEXT`` WAS the documented
    state, so falling back to it cost nothing. Since
    SPEC-PRIVACY-PII-POLICY-ADMIN-001 D2 made the whole return set default-on,
    the same fallback silently switches masking OFF for seven entity types at
    exactly the moment the control is most wanted — and it does so on a
    portal-api hiccup, while LiteLLM and chat keep serving. That is not a
    smaller mistake than the alternative; it is the larger one.

    Preference order, and why:

    1. **Last-good for this org.** A tenant that deliberately switched a type
       off must not have it switched back on by an outage, so whatever that
       tenant last actually chose wins. The telemetry half rides along
       deliberately — a stale ``off`` stays off.
    2. **The D2 default set** for an org we have an id for but have never
       resolved. This is the cold case: a LiteLLM worker restarted (a deploy,
       a crash) while portal-api is unreachable, so nothing is warm and every
       tenant looks new. Serving ``EMPTY_CONTEXT`` there would reintroduce
       exactly the leak this function exists to close, at the worst possible
       moment — correlated outages are what a bad deploy looks like.
       Over-masking a tenant who had opted a type out costs a slightly worse
       answer for the length of the outage, and the value is restored in the
       response either way; under-masking sends their customers' data to a
       model provider and cannot be taken back.
    3. **``EMPTY_CONTEXT``** only when there is no ``org_id`` at all — the
       caller checks that before reaching here. No identity means no tenant
       whose data this could be, and none who could have consented to
       telemetry.

    The default in (2) is spelled ``RETURN_SET_ENTITIES`` rather than fetched,
    because portal-api is by definition unreachable at this point. It is the
    same set portal-api would return for a tenant that has never touched the
    setting (``PII_DEFAULT_MASKED_ENTITIES``); the duplication note in
    ``klai_pii_entities`` is what keeps the two in step.
    """
    stale = _last_good.get(org_id)
    if stale is None:
        logger.warning(
            "pii_org_policy_cold_degraded org_id=%s reason=%s — never resolved, "
            "masking the full default set until portal-api answers",
            org_id,
            reason,
        )
        return OrgPiiContext(
            entities=RETURN_SET_ENTITIES,
            telemetry_level=TELEMETRY_LEVEL_WHEN_UNKNOWN,
        )
    logger.warning(
        "pii_org_policy_degraded org_id=%s reason=%s — serving last known good policy "
        "(%s entities, telemetry=%s)",
        org_id,
        reason,
        len(stale.entities),
        stale.telemetry_level,
    )
    return stale


async def resolve_org_pii_context(org_id: Any) -> OrgPiiContext:
    """Return this org's masked-entity set and its telemetry mode.

    A missing ``org_id`` (master-key / widget calls, the same shape
    ``klai_pii_observe.py`` names as a KlaiKnowledgeHook blind spot) returns
    ``EMPTY_CONTEXT``: there is no org to look a policy up for, so none of the
    optional entities can be enabled and no tenant has authorised telemetry.

    Every OTHER failure — no ``PORTAL_INTERNAL_SECRET``, a network error, a
    timeout, a non-2xx, a malformed payload — goes through ``_degraded``,
    which serves this org's last successfully resolved policy, or the D2
    default set if it has never resolved one. None of these raise: an
    unresolvable policy is not REQ-10's analyzer-unreachable case (that one
    fails the request).

    Whatever ``_degraded`` returns, its telemetry half is the tenant's own
    stale value or ``off`` — see ``TELEMETRY_LEVEL_WHEN_UNKNOWN``. Masking
    more than a tenant asked for is recoverable; saying things about a tenant
    who never consented is not.

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
        return EMPTY_CONTEXT

    cached = _cache_get(org_id)
    if cached is not None:
        return cached

    if not PORTAL_INTERNAL_SECRET:
        # The rule below matches the word "SECRET" in the FORMAT STRING, not a
        # logged value — only org_id is interpolated, and the message says the
        # secret is ABSENT. Documented false-positive class in
        # .claude/rules/klai/infra/deploy.md ("Semgrep false positives on
        # OAuth log messages"), whose prescribed fix is this annotation.
        # The nosemgrep comment must be the line IMMEDIATELY before the match.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.warning(
            "pii_org_policy_no_secret org_id=%s — PORTAL_INTERNAL_SECRET not set, "
            "fail-closed to no optional entities",
            org_id,
        )
        return _degraded(org_id, "no_internal_secret")

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
        return _degraded(org_id, "fetch_failed")

    enabled = payload.get("enabled_entities") if isinstance(payload, dict) else None
    if not isinstance(enabled, list):
        return _degraded(org_id, "malformed_payload")

    policy = frozenset(e for e in enabled if isinstance(e, str)) & RETURN_SET_ENTITIES

    # An absent, non-string or unrecognised value is treated as "unknown", not
    # coerced to a default: a portal-api that does not send this field yet has
    # not authorised anything, and a value we do not recognise is not a value
    # we can honour.
    raw_level = payload.get("telemetry_level")
    telemetry_level = (
        raw_level if raw_level in ("off", "shadow", "full") else TELEMETRY_LEVEL_WHEN_UNKNOWN
    )

    context = OrgPiiContext(entities=policy, telemetry_level=telemetry_level)
    _cache_set(org_id, context)
    return context
