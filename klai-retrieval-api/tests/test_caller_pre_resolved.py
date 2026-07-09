"""Tests for the caller-pre-resolved coreference skip contract.

Contract: retrieval-api must not run a second coreference rewrite when the
caller already resolved coreference (the litellm hook's query-rewrite). The
signal is a distinct ``raw_query`` alongside an already-rewritten ``query``.
This eliminates the double rewrite (hook + retrieval-api) that compounded
query-rewrite drift.
"""

from __future__ import annotations

from retrieval_api.api.retrieve import _caller_pre_resolved
from retrieval_api.models import RetrieveRequest


def _req(
    query: str,
    raw_query: str | None,
    coreference_resolved: bool | None = None,
) -> RetrieveRequest:
    return RetrieveRequest(
        query=query,
        raw_query=raw_query,
        org_id="org-1",
        scope="org",
        coreference_resolved=coreference_resolved,
    )


class TestCallerPreResolved:
    def test_distinct_raw_query_is_pre_resolved(self):
        # litellm hook: query=rewritten, raw_query=original user text.
        req = _req(
            "Voys Salesforce CRM-koppeling Bubble RedCactus", "Staat er iets over salesforce?"
        )
        assert _caller_pre_resolved(req) is True

    def test_equal_raw_query_is_not_pre_resolved(self):
        # knowledge-mcp: raw_query == query (it does not rewrite).
        req = _req("hoe voeg ik een gebruiker toe?", "hoe voeg ik een gebruiker toe?")
        assert _caller_pre_resolved(req) is False

    def test_absent_raw_query_is_not_pre_resolved(self):
        # partner / focus: raw_query omitted (defaults to None).
        req = _req("hoe voeg ik een gebruiker toe?", None)
        assert _caller_pre_resolved(req) is False

    def test_empty_raw_query_is_not_pre_resolved(self):
        req = _req("hoe voeg ik een gebruiker toe?", "")
        assert _caller_pre_resolved(req) is False


class TestExplicitCoreferenceResolvedFlag:
    """The explicit ``coreference_resolved`` flag overrides the legacy
    raw_query != query heuristic.

    Regression contract: when the litellm hook's destructive-rewrite guard
    fires it discards the rewrite and sends ``raw_query == query`` — under the
    legacy heuristic that re-triggered retrieval-api's own unguarded
    coreference rewrite, reopening the exact incident the guard blocked
    (2026-07-09, request daf04d03: "Wat weet je over klai?" rewritten to an
    unrelated Yealink query).
    """

    def test_flag_true_with_equal_queries_is_pre_resolved(self):
        # Guard-fire shape: rewrite discarded, raw_query == query, but the
        # coreference decision WAS made upstream.
        req = _req(
            "Wat weet je over klai?",
            "Wat weet je over klai?",
            coreference_resolved=True,
        )
        assert _caller_pre_resolved(req) is True

    def test_flag_false_with_distinct_queries_is_not_pre_resolved(self):
        # Caller explicitly says its rewrite step did not complete (e.g.
        # rewrite LLM timeout) — retrieval-api may resolve, even though the
        # queries happen to differ.
        req = _req(
            "Voys Salesforce CRM-koppeling",
            "Staat er iets over salesforce?",
            coreference_resolved=False,
        )
        assert _caller_pre_resolved(req) is False

    def test_flag_absent_falls_back_to_legacy_heuristic(self):
        # Older callers without the field keep the raw_query != query contract.
        req = _req(
            "Voys Salesforce CRM-koppeling",
            "Staat er iets over salesforce?",
            coreference_resolved=None,
        )
        assert _caller_pre_resolved(req) is True
