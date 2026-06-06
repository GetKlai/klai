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


def _req(query: str, raw_query: str | None) -> RetrieveRequest:
    return RetrieveRequest(query=query, raw_query=raw_query, org_id="org-1", scope="org")


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
