"""SPEC-RAG-TAXONOMY-001 — _rewrite_and_classify + fetch helpers unit tests.

Tests:
- _rewrite_and_classify: skip when no tree, happy path, anti-hallucination guard,
  fail-open on LLM error, falls back to plain rewrite when tree is empty.
- _fetch_taxonomy_tree: returns list on 200, [] on 4xx/5xx/timeout.
- _fetch_taxonomy_coverage: returns float on 200, 0.0 on error.
- _format_taxonomy_for_prompt: truncates at max_nodes.

litellm is not installed locally (runs in Docker), so we mock the import.
"""

from __future__ import annotations

import importlib
import json
import sys
import types

import httpx
import pytest


# ---------------------------------------------------------------------------
# litellm mock (required for import)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_litellm():
    litellm_mod = types.ModuleType("litellm")
    integrations_mod = types.ModuleType("litellm.integrations")
    custom_logger_mod = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:
        async def async_pre_call_hook(self, *args, **kwargs):
            pass

    custom_logger_mod.CustomLogger = CustomLogger
    integrations_mod.custom_logger = custom_logger_mod
    litellm_mod.integrations = integrations_mod

    sys.modules["litellm"] = litellm_mod
    sys.modules["litellm.integrations"] = integrations_mod
    sys.modules["litellm.integrations.custom_logger"] = custom_logger_mod

    yield


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------


def _load_hook(monkeypatch, extra_env=None):
    env = {
        "PORTAL_INTERNAL_SECRET": "test-portal-secret",
        "RETRIEVAL_INTERNAL_SECRET": "test-retrieval-secret",
        "KNOWLEDGE_RETRIEVE_URL": "http://retrieval-api:8040/retrieve",
        "PORTAL_API_URL": "http://portal-api:8000",
        "MISTRAL_API_KEY": "test-mistral-key",
        "TAXONOMY_ENABLED": "true",
        "KLAI_TAXONOMY_COVERAGE_THRESHOLD": "0.30",
    }
    if extra_env:
        env.update(extra_env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    sys.modules.pop("klai_knowledge", None)
    import klai_knowledge

    importlib.reload(klai_knowledge)
    return klai_knowledge


# ---------------------------------------------------------------------------
# Transport mocks
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    """Return a fixed status + JSON body for every request."""

    def __init__(self, status_code: int, json_body=None) -> None:
        self._status_code = status_code
        self._json_body = json_body if json_body is not None else {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=self._status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(self._json_body).encode(),
            request=request,
        )


class _RoutedTransport(httpx.AsyncBaseTransport):
    """Return different responses based on the request URL path."""

    def __init__(self, routes: dict) -> None:
        # routes = {"/internal/v1/taxonomy/tree": (200, [...]), ...}
        self._routes = routes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        status, body = self._routes.get(path, (404, {"detail": "not found"}))
        return httpx.Response(
            status_code=status,
            headers={"content-type": "application/json"},
            content=json.dumps(body).encode(),
            request=request,
        )


def _llm_json_response(rewritten_query: str, taxonomy_node_ids: list) -> dict:
    """Build a Mistral-style JSON choices response."""
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "rewritten_query": rewritten_query,
                            "taxonomy_node_ids": taxonomy_node_ids,
                        }
                    ),
                }
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 20},
    }


_TREE = [
    {"id": 1, "name": "SSO", "parent_id": None, "depth": 0},
    {"id": 2, "name": "SAML", "parent_id": 1, "depth": 1},
    {"id": 3, "name": "OAuth", "parent_id": 1, "depth": 1},
]

_HISTORY = [
    {"role": "user", "content": "Hoe configureer je SAML?"},
    {"role": "assistant", "content": "Je moet eerst de IdP instellen."},
]


# ---------------------------------------------------------------------------
# _rewrite_and_classify — skip conditions
# ---------------------------------------------------------------------------


class TestRewriteAndClassifySkips:
    @pytest.mark.asyncio
    async def test_skips_empty_query(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        rewritten, ids, meta = await hook._rewrite_and_classify("", _HISTORY, _TREE)
        assert rewritten == ""
        assert ids == []
        assert meta["skipped"] == "empty_query"

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, monkeypatch):
        hook = _load_hook(monkeypatch, extra_env={"QUERY_REWRITE_ENABLED": "false"})
        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", _HISTORY, _TREE
        )
        assert rewritten == "Wat is SAML?"
        assert ids == []
        assert meta["skipped"] == "disabled"

    @pytest.mark.asyncio
    async def test_skips_when_no_api_key(self, monkeypatch):
        hook = _load_hook(monkeypatch, extra_env={"MISTRAL_API_KEY": ""})
        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", _HISTORY, _TREE
        )
        assert ids == []
        assert meta["skipped"] == "no_api_key"

    @pytest.mark.asyncio
    async def test_skips_classify_when_no_history_and_no_tree(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        rewritten, ids, meta = await hook._rewrite_and_classify("Wat is SAML?", [], [])
        # No history, no tree → skip (no_history_no_tree)
        assert rewritten == "Wat is SAML?"
        assert ids == []
        assert meta["skipped"] == "no_history_no_tree"

    @pytest.mark.asyncio
    async def test_falls_back_to_plain_rewrite_when_tree_empty(self, monkeypatch):
        """Empty tree → falls back to _rewrite_query (plain text prompt, no classify)."""
        hook = _load_hook(monkeypatch)
        rewritten_str = "Wat is de status van de SAML-configuratie?"
        transport = _MockTransport(
            status_code=200,
            json_body={
                "choices": [
                    {"message": {"role": "assistant", "content": rewritten_str}}
                ],
                "usage": {"prompt_tokens": 80, "completion_tokens": 20},
            },
        )
        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is de status?", _HISTORY, [], _transport=transport
        )
        assert rewritten == rewritten_str
        assert ids == []
        # Meta is from plain _rewrite_query: was_changed should be True
        assert meta["was_changed"] is True


# ---------------------------------------------------------------------------
# _rewrite_and_classify — happy path
# ---------------------------------------------------------------------------


class TestRewriteAndClassifyHappyPath:
    @pytest.mark.asyncio
    async def test_returns_rewritten_query_and_ids(self, monkeypatch):
        """Full happy path: LLM returns rewritten query + valid IDs."""
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200,
            json_body=_llm_json_response(
                "Hoe configureer je SAML-authenticatie?", [1, 2]
            ),
        )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Hoe doe je dat?", _HISTORY, _TREE, _transport=transport
        )

        assert rewritten == "Hoe configureer je SAML-authenticatie?"
        assert 1 in ids
        assert 2 in ids
        assert meta["was_changed"] is True
        assert meta["rewrite_ms"] >= 0

    @pytest.mark.asyncio
    async def test_drops_hallucinated_ids(self, monkeypatch):
        """Anti-hallucination guard (REQ-4): IDs not in tree are filtered out."""
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200,
            # IDs 1, 2, 3 are valid; 99 and 1000 are hallucinated
            json_body=_llm_json_response("Hoe configureer je SAML?", [1, 2, 99, 1000]),
        )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Hoe doe je dat?", _HISTORY, _TREE, _transport=transport
        )

        assert set(ids) == {1, 2}
        assert 99 not in ids
        assert 1000 not in ids

    @pytest.mark.asyncio
    async def test_returns_empty_ids_when_llm_classifies_none(self, monkeypatch):
        """LLM returns empty taxonomy_node_ids → [] is valid (query is off-topic)."""
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200,
            json_body=_llm_json_response("Hoe reset ik mijn wachtwoord?", []),
        )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wachtwoord vergeten", _HISTORY, _TREE, _transport=transport
        )

        assert ids == []
        assert rewritten == "Hoe reset ik mijn wachtwoord?"

    @pytest.mark.asyncio
    async def test_was_changed_false_when_query_unchanged(self, monkeypatch):
        """If LLM returns the same query text, was_changed is False."""
        hook = _load_hook(monkeypatch)
        raw = "Wat is OAuth?"
        transport = _MockTransport(
            status_code=200,
            json_body=_llm_json_response(raw, [3]),
        )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            raw, _HISTORY, _TREE, _transport=transport
        )

        assert rewritten == raw
        assert meta["was_changed"] is False
        assert 3 in ids


# ---------------------------------------------------------------------------
# _rewrite_and_classify — failure / fail-open
# ---------------------------------------------------------------------------


class TestRewriteAndClassifyFailOpen:
    @pytest.mark.asyncio
    async def test_falls_back_on_500(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=500, json_body={"detail": "boom"})

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", _HISTORY, _TREE, _transport=transport
        )

        # Must return raw query unchanged (fail-open REQ-2)
        assert rewritten == "Wat is SAML?"
        assert ids == []
        assert meta["skipped"] == "exception"
        assert "error" in meta

    @pytest.mark.asyncio
    async def test_falls_back_on_malformed_json(self, monkeypatch):
        """LLM returns non-JSON content → fail-open."""
        hook = _load_hook(monkeypatch)

        class _BadJsonTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                return httpx.Response(
                    status_code=200,
                    headers={"content-type": "application/json"},
                    content=b'{"choices": [{"message": {"role": "assistant", "content": "not valid json at all"}}]}',
                    request=request,
                )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", _HISTORY, _TREE, _transport=_BadJsonTransport()
        )

        # json.loads("not valid json at all") raises → parsed = {} → rewritten_query absent
        assert rewritten == "Wat is SAML?"
        assert ids == []

    @pytest.mark.asyncio
    async def test_falls_back_when_rewritten_query_empty(self, monkeypatch):
        """LLM returns empty rewritten_query → fall back to raw."""
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200,
            json_body=_llm_json_response("", [1]),
        )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", _HISTORY, _TREE, _transport=transport
        )

        assert rewritten == "Wat is SAML?"
        assert ids == []
        assert meta.get("skipped") == "empty_rewritten_query"


# ---------------------------------------------------------------------------
# _fetch_taxonomy_tree
# ---------------------------------------------------------------------------


class TestFetchTaxonomyTree:
    @pytest.mark.asyncio
    async def test_returns_list_on_200(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        tree_data = [{"id": 1, "name": "Root", "parent_id": None, "depth": 0}]
        transport = _MockTransport(status_code=200, json_body=tree_data)

        result = await hook._fetch_taxonomy_tree("org-1", "kb-1", _transport=transport)

        assert result == tree_data

    @pytest.mark.asyncio
    async def test_returns_empty_on_404(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=404, json_body={"detail": "not found"})

        result = await hook._fetch_taxonomy_tree("org-1", "kb-x", _transport=transport)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_500(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=500, json_body={"detail": "error"})

        result = await hook._fetch_taxonomy_tree("org-1", "kb-1", _transport=transport)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_taxonomy_disabled(self, monkeypatch):
        hook = _load_hook(monkeypatch, extra_env={"TAXONOMY_ENABLED": "false"})

        # No transport passed; if it made a real request it would fail
        result = await hook._fetch_taxonomy_tree("org-1", "kb-1")

        assert result == []


# ---------------------------------------------------------------------------
# _fetch_taxonomy_coverage
# ---------------------------------------------------------------------------


class TestFetchTaxonomyCoverage:
    @pytest.mark.asyncio
    async def test_returns_float_on_200(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=200, json_body={"coverage": 0.65})

        result = await hook._fetch_taxonomy_coverage(
            "org-1", "kb-1", _transport=transport
        )

        assert abs(result - 0.65) < 1e-6

    @pytest.mark.asyncio
    async def test_returns_zero_on_error(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=500, json_body={"detail": "boom"})

        result = await hook._fetch_taxonomy_coverage(
            "org-1", "kb-1", _transport=transport
        )

        assert result == 0.0

    @pytest.mark.asyncio
    async def test_returns_zero_when_key_missing(self, monkeypatch):
        """Response JSON without 'coverage' key → 0.0."""
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=200, json_body={})

        result = await hook._fetch_taxonomy_coverage(
            "org-1", "kb-1", _transport=transport
        )

        assert result == 0.0

    @pytest.mark.asyncio
    async def test_returns_zero_when_disabled(self, monkeypatch):
        hook = _load_hook(monkeypatch, extra_env={"TAXONOMY_ENABLED": "false"})

        result = await hook._fetch_taxonomy_coverage("org-1", "kb-1")

        assert result == 0.0


# ---------------------------------------------------------------------------
# _format_taxonomy_for_prompt
# ---------------------------------------------------------------------------


class TestFormatTaxonomyForPrompt:
    def test_formats_nodes_as_id_name_lines(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        tree = [
            {"id": 1, "name": "SSO", "parent_id": None, "depth": 0},
            {"id": 2, "name": "SAML", "parent_id": 1, "depth": 1},
        ]
        result = hook._format_taxonomy_for_prompt(tree)
        assert "id=1: SSO" in result
        assert "id=2: SAML" in result

    def test_returns_none_for_empty_tree(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        result = hook._format_taxonomy_for_prompt([])
        assert result == "(none)"

    def test_truncates_at_max_nodes(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        tree = [
            {"id": i, "name": f"Node {i}", "parent_id": None, "depth": 0}
            for i in range(50)
        ]

        result = hook._format_taxonomy_for_prompt(tree, max_nodes=10)

        # Exactly 10 node lines + 1 truncation line
        lines = result.splitlines()
        node_lines = [ln for ln in lines if ln.startswith("- id=")]
        assert len(node_lines) == 10
        assert "more nodes omitted" in result

    def test_no_truncation_line_when_within_limit(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        tree = [
            {"id": i, "name": f"Node {i}", "parent_id": None, "depth": 0}
            for i in range(5)
        ]

        result = hook._format_taxonomy_for_prompt(tree, max_nodes=10)

        assert "omitted" not in result
        assert result.count("- id=") == 5
