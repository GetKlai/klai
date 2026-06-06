"""Feature-gate and retrieval-scope policy for path-A KB chat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

KbGateAction = Literal["continue", "general", "strict_no_kb"]


@dataclass(frozen=True)
class KbFeatureGateDecision:
    action: KbGateAction
    kb_narrow: bool
    strict_no_kb_reason: str | None = None


@dataclass(frozen=True)
class KbRetrievalScopeDecision:
    action: KbGateAction
    kb_narrow: bool
    kb_personal: bool
    kb_slugs: list[str] | None
    scope: str | None = None
    kb_slugs_for_request: list[str] | None = None
    include_owned_private_kbs: bool = False
    kbs_in_scope: list[str] | None = None
    strict_no_kb_reason: str | None = None


@dataclass(frozen=True)
class KbTaxonomyDecision:
    applied: bool
    skip_reason: str | None = None


def resolve_kb_feature_gate(feature: dict) -> KbFeatureGateDecision:
    """Resolve whether the feature-level gate should continue into retrieval."""
    kb_narrow = bool(feature.get("kb_narrow", False))
    if not feature.get("enabled", False):
        return KbFeatureGateDecision(
            action="strict_no_kb" if kb_narrow else "general",
            kb_narrow=kb_narrow,
            strict_no_kb_reason="kb-feature-disabled" if kb_narrow else None,
        )
    if not feature.get("kb_retrieval_enabled", True):
        return KbFeatureGateDecision(
            action="strict_no_kb" if kb_narrow else "general",
            kb_narrow=kb_narrow,
            strict_no_kb_reason="kb-retrieval-disabled" if kb_narrow else None,
        )
    return KbFeatureGateDecision(action="continue", kb_narrow=kb_narrow)


def resolve_kb_retrieval_scope(feature: dict) -> KbRetrievalScopeDecision:
    """Translate portal KB preferences into retrieval-api scope fields.

    ``kb_slugs_filter`` is tri-state:
    * ``None`` means all org KBs.
    * ``[]`` means no org KBs.
    * non-empty list means an explicit subset.

    The distinction between ``None`` and ``[]`` is deliberate. The hook used
    to collapse both through a truthiness check, so a user who turned every
    org collection off could still receive all org KB chunks.
    """
    kb_personal = bool(feature.get("kb_personal_enabled", True))
    kb_slugs = feature.get("kb_slugs_filter")
    kb_narrow = bool(feature.get("kb_narrow", False))

    if not kb_personal and kb_slugs == []:
        return KbRetrievalScopeDecision(
            action="strict_no_kb" if kb_narrow else "general",
            kb_narrow=kb_narrow,
            kb_personal=kb_personal,
            kb_slugs=kb_slugs,
            strict_no_kb_reason="kb-scopes-disabled" if kb_narrow else None,
        )

    if kb_personal and kb_slugs == []:
        scope = "personal"
        kb_slugs_for_request = None
    else:
        scope = "both" if kb_personal else "org"
        kb_slugs_for_request = kb_slugs if kb_slugs else None

    return KbRetrievalScopeDecision(
        action="continue",
        kb_narrow=kb_narrow,
        kb_personal=kb_personal,
        kb_slugs=kb_slugs,
        scope=scope,
        kb_slugs_for_request=kb_slugs_for_request,
        include_owned_private_kbs=(scope == "both" and kb_slugs is None),
        kbs_in_scope=list(kb_slugs_for_request) if kb_slugs_for_request else [],
    )


def build_retrieve_body(
    *,
    rewritten_query: str,
    raw_query: str,
    org_id: object,
    user_id: object,
    top_k: int,
    conversation_history: list,
    telemetry_level: object,
    scope_decision: KbRetrievalScopeDecision,
    taxonomy_applied: bool,
    classified_node_ids: list,
) -> dict:
    """Build the retrieval-api request body from resolved policy state."""
    if scope_decision.action != "continue" or scope_decision.scope is None:
        raise ValueError("retrieve body requires a continuing KB scope decision")

    body: dict = {
        "query": rewritten_query,
        "raw_query": raw_query,
        "org_id": org_id,
        "user_id": user_id,
        "scope": scope_decision.scope,
        "top_k": top_k,
        "conversation_history": conversation_history,
        "telemetry_level": telemetry_level,
        "kb_narrow": scope_decision.kb_narrow,
    }
    if scope_decision.kb_slugs_for_request:
        body["kb_slugs"] = scope_decision.kb_slugs_for_request
    elif scope_decision.include_owned_private_kbs:
        body["include_owned_private_kbs"] = True
    if taxonomy_applied:
        body["taxonomy_node_ids"] = classified_node_ids
    return body


def resolve_kb_taxonomy_decision(
    *,
    taxonomy_enabled: bool,
    kbs_in_scope: list[str],
    kbs_with_coverage: list[str],
    classified_node_ids: list,
) -> KbTaxonomyDecision:
    """Resolve whether taxonomy node filters should be sent to retrieval-api."""
    if not taxonomy_enabled:
        return KbTaxonomyDecision(applied=False, skip_reason="disabled")
    if not kbs_in_scope:
        return KbTaxonomyDecision(applied=False, skip_reason="no_kbs_in_scope")
    if not kbs_with_coverage:
        return KbTaxonomyDecision(applied=False, skip_reason="all_kbs_low_coverage")
    if not classified_node_ids:
        return KbTaxonomyDecision(applied=False, skip_reason="empty_classify")
    return KbTaxonomyDecision(applied=True)
