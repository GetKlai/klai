"""SPEC-RAG-TAXONOMY-001 (multi-KB) — _rewrite_and_classify + fetch helpers.

Tests cover the v2 multi-KB shape:
- ``_rewrite_and_classify`` accepts ``dict[str, list[dict]]`` (multi-KB)
  AND ``list[dict]`` (legacy single-KB) for backward compat.
- ``_fetch_taxonomy_trees`` calls ``/internal/v1/taxonomy/trees`` with
  repeated ``kb_slugs`` query params and a Redis cache hit short-circuit.
- ``_fetch_taxonomy_coverage`` returns ``{kb_slug: 0.0|1.0}`` via the
  multi-KB endpoint.
- ``_format_taxonomy_for_prompt`` renders both shapes with KB-context
  labels for the multi-KB shape.
- ``_flatten_trees`` produces a single flat list across all KBs.

litellm is not installed locally (runs in Docker), so we mock the import.
"""

from __future__ import annotations

import importlib
import json
import sys
import types

import httpx
import pytest

from tests.klai_module_reset import reset_klai_kb_modules


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

    reset_klai_kb_modules()
    import klai_knowledge

    importlib.reload(klai_knowledge)
    return klai_knowledge


# ---------------------------------------------------------------------------
# Cache stub (mimics LiteLLM DualCache surface used by the hook)
# ---------------------------------------------------------------------------


class _StubCache:
    """In-memory cache supporting async_get_cache / async_set_cache."""

    def __init__(self) -> None:
        self.store: dict = {}
        self.gets: list[str] = []
        self.sets: list[tuple[str, object, int | None]] = []

    async def async_get_cache(self, key: str):
        self.gets.append(key)
        return self.store.get(key)

    async def async_set_cache(self, key: str, value, ttl: int | None = None) -> None:
        self.sets.append((key, value, ttl))
        self.store[key] = value


# ---------------------------------------------------------------------------
# Transport mocks
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    """Return a fixed status + JSON body for every request."""

    def __init__(self, status_code: int, json_body=None) -> None:
        self._status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            status_code=self._status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(self._json_body).encode(),
            request=request,
        )


class _RoutedTransport(httpx.AsyncBaseTransport):
    """Return different responses based on the request URL path."""

    def __init__(self, routes: dict) -> None:
        self._routes = routes
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
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


def _llm_text_response(content: str) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 80, "completion_tokens": 20},
    }


# Multi-KB sample: support and billing each have their own subtree.
# Node IDs are globally unique (single PK), so the merged set covers both.
_TREES_MULTI = {
    "support": [
        {
            "id": 1,
            "kb_slug": "support",
            "name": "SSO",
            "slug": "sso",
            "parent_id": None,
        },
        {"id": 2, "kb_slug": "support", "name": "SAML", "slug": "saml", "parent_id": 1},
    ],
    "billing": [
        {
            "id": 10,
            "kb_slug": "billing",
            "name": "Invoices",
            "slug": "invoices",
            "parent_id": None,
        },
        {
            "id": 11,
            "kb_slug": "billing",
            "name": "Refunds",
            "slug": "refunds",
            "parent_id": 10,
        },
    ],
}

_TREE_LEGACY = [
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
        rewritten, ids, meta = await hook._rewrite_and_classify(
            "", _HISTORY, _TREES_MULTI
        )
        assert rewritten == ""
        assert ids == []
        assert meta["skipped"] == "empty_query"

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, monkeypatch):
        hook = _load_hook(monkeypatch, extra_env={"QUERY_REWRITE_ENABLED": "false"})
        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", _HISTORY, _TREES_MULTI
        )
        assert rewritten == "Wat is SAML?"
        assert ids == []
        assert meta["skipped"] == "disabled"

    @pytest.mark.asyncio
    async def test_skips_when_no_api_key(self, monkeypatch):
        hook = _load_hook(monkeypatch, extra_env={"MISTRAL_API_KEY": ""})
        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", _HISTORY, _TREES_MULTI
        )
        assert ids == []
        assert meta["skipped"] == "no_api_key"

    @pytest.mark.asyncio
    async def test_plain_rewrite_runs_when_no_history_and_empty_dict(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200,
            json_body=_llm_text_response("Wat is SAML identity provider?"),
        )
        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", [], {}, _transport=transport
        )
        assert rewritten == "Wat is SAML identity provider?"
        assert ids == []
        assert meta["prompt_variant"] == "plain"
        assert "skipped" not in meta

    @pytest.mark.asyncio
    async def test_plain_rewrite_runs_when_no_history_and_empty_list(self, monkeypatch):
        """Legacy list shape: empty list also uses the plain rewrite path."""
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200,
            json_body=_llm_text_response("Wat is SAML identity provider?"),
        )
        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", [], [], _transport=transport
        )
        assert rewritten == "Wat is SAML identity provider?"
        assert ids == []
        assert meta["prompt_variant"] == "plain"

    @pytest.mark.asyncio
    async def test_falls_back_to_plain_rewrite_when_dict_empty_with_history(
        self, monkeypatch
    ):
        """Empty trees dict + history → plain _rewrite_query, no classify."""
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
            "Wat is de status?", _HISTORY, {}, _transport=transport
        )
        assert rewritten == rewritten_str
        assert ids == []
        assert meta["was_changed"] is True

    @pytest.mark.asyncio
    async def test_rejects_destructive_rewrite_and_drops_classification(
        self, monkeypatch
    ):
        """A bad rewrite and its taxonomy IDs must not narrow retrieval."""
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200,
            json_body=_llm_json_response(
                "Hoe stel ik een Yealink toestel in en openingstijden configureren",
                [1, 2],
            ),
        )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat weet je over klai?",
            _HISTORY,
            _TREES_MULTI,
            _transport=transport,
        )

        assert rewritten == "Wat weet je over klai?"
        assert ids == []
        assert meta["skipped"] == "destructive_rewrite"
        assert meta["was_changed"] is False
        assert meta["dropped_salient_tokens"] == ["klai"]


# ---------------------------------------------------------------------------
# _rewrite_and_classify — multi-KB happy path
# ---------------------------------------------------------------------------


class TestRewriteAndClassifyMultiKB:
    @pytest.mark.asyncio
    async def test_returns_ids_from_any_kb(self, monkeypatch):
        """Classifier may pick IDs from any KB in the merged tree."""
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200,
            # Picks IDs from BOTH support (1, 2) and billing (10).
            json_body=_llm_json_response("Hoe regel ik SAML en facturen?", [1, 2, 10]),
        )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Hoe doe je dat?", _HISTORY, _TREES_MULTI, _transport=transport
        )

        assert rewritten == "Hoe regel ik SAML en facturen?"
        assert set(ids) == {1, 2, 10}
        assert meta["was_changed"] is True

    @pytest.mark.asyncio
    async def test_drops_hallucinated_ids_across_kbs(self, monkeypatch):
        """Anti-hallucination guard: IDs not in ANY KB tree are filtered out."""
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200,
            # Valid: 1, 11. Hallucinated: 99, 1000.
            json_body=_llm_json_response("Hoe regel ik SAML?", [1, 11, 99, 1000]),
        )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Hoe doe je dat?", _HISTORY, _TREES_MULTI, _transport=transport
        )

        assert set(ids) == {1, 11}

    @pytest.mark.asyncio
    async def test_legacy_list_shape_still_works(self, monkeypatch):
        """Backward compat: list[dict] tree shape continues to work."""
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200,
            json_body=_llm_json_response("Hoe configureer je SAML?", [1, 2]),
        )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Hoe doe je dat?", _HISTORY, _TREE_LEGACY, _transport=transport
        )

        assert set(ids) == {1, 2}
        assert meta["was_changed"] is True


# ---------------------------------------------------------------------------
# _rewrite_and_classify — failure / fail-open
# ---------------------------------------------------------------------------


class TestRewriteAndClassifyFailOpen:
    @pytest.mark.asyncio
    async def test_falls_back_on_500(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=500, json_body={"detail": "boom"})

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", _HISTORY, _TREES_MULTI, _transport=transport
        )

        assert rewritten == "Wat is SAML?"
        assert ids == []
        assert meta["skipped"] == "exception"
        assert "error" in meta

    @pytest.mark.asyncio
    async def test_falls_back_on_malformed_json(self, monkeypatch):
        hook = _load_hook(monkeypatch)

        class _BadJsonTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                return httpx.Response(
                    status_code=200,
                    headers={"content-type": "application/json"},
                    content=(
                        b'{"choices": [{"message": {"role": "assistant", '
                        b'"content": "not valid json at all"}}]}'
                    ),
                    request=request,
                )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", _HISTORY, _TREES_MULTI, _transport=_BadJsonTransport()
        )

        assert rewritten == "Wat is SAML?"
        assert ids == []

    @pytest.mark.asyncio
    async def test_falls_back_when_rewritten_query_empty(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200,
            json_body=_llm_json_response("", [1]),
        )

        rewritten, ids, meta = await hook._rewrite_and_classify(
            "Wat is SAML?", _HISTORY, _TREES_MULTI, _transport=transport
        )

        assert rewritten == "Wat is SAML?"
        assert ids == []
        assert meta.get("skipped") == "empty_rewritten_query"


# ---------------------------------------------------------------------------
# _fetch_taxonomy_trees (multi-KB)
# ---------------------------------------------------------------------------


class TestFetchTaxonomyTrees:
    @pytest.mark.asyncio
    async def test_returns_dict_on_200(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=200, json_body=_TREES_MULTI)
        cache = _StubCache()

        result = await hook._fetch_taxonomy_trees(
            "org-1", ["support", "billing"], cache, _transport=transport
        )

        assert "support" in result
        assert "billing" in result
        # One request, with kb_slugs as repeated query params.
        assert len(transport.requests) == 1
        req_url = str(transport.requests[0].url)
        assert "kb_slugs=support" in req_url
        assert "kb_slugs=billing" in req_url

    @pytest.mark.asyncio
    async def test_redis_cache_hit_short_circuits_request(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=200, json_body={"unused": []})
        cache = _StubCache()
        cache.store["tax_trees:org-1:billing,support"] = _TREES_MULTI

        result = await hook._fetch_taxonomy_trees(
            "org-1", ["support", "billing"], cache, _transport=transport
        )

        assert result == _TREES_MULTI
        # No HTTP request fired.
        assert transport.requests == []

    @pytest.mark.asyncio
    async def test_writes_to_cache_on_miss(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=200, json_body=_TREES_MULTI)
        cache = _StubCache()

        await hook._fetch_taxonomy_trees(
            "org-1", ["support", "billing"], cache, _transport=transport
        )

        keys_set = [k for k, _, _ in cache.sets]
        assert "tax_trees:org-1:billing,support" in keys_set

    @pytest.mark.asyncio
    async def test_returns_empty_on_5xx(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=500, json_body={"detail": "boom"})
        cache = _StubCache()

        result = await hook._fetch_taxonomy_trees(
            "org-1", ["support"], cache, _transport=transport
        )

        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_disabled(self, monkeypatch):
        hook = _load_hook(monkeypatch, extra_env={"TAXONOMY_ENABLED": "false"})
        cache = _StubCache()

        result = await hook._fetch_taxonomy_trees("org-1", ["support"], cache)

        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_kb_slugs_empty(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        cache = _StubCache()

        result = await hook._fetch_taxonomy_trees("org-1", [], cache)

        assert result == {}

    @pytest.mark.asyncio
    async def test_skips_when_too_many_kbs(self, monkeypatch):
        """Cap at _MAX_KBS_FOR_TAXONOMY (5) — returns {} above the cap."""
        hook = _load_hook(monkeypatch)
        cache = _StubCache()
        too_many = ["kb1", "kb2", "kb3", "kb4", "kb5", "kb6"]

        result = await hook._fetch_taxonomy_trees("org-1", too_many, cache)

        assert result == {}


# ---------------------------------------------------------------------------
# _fetch_taxonomy_coverage (multi-KB)
# ---------------------------------------------------------------------------


class TestFetchTaxonomyCoverage:
    @pytest.mark.asyncio
    async def test_returns_per_kb_floats_on_200(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(
            status_code=200, json_body={"support": 1.0, "billing": 0.0}
        )
        cache = _StubCache()

        result = await hook._fetch_taxonomy_coverage(
            "org-1", ["support", "billing"], cache, _transport=transport
        )

        assert result == {"support": 1.0, "billing": 0.0}

    @pytest.mark.asyncio
    async def test_missing_kb_defaults_to_zero(self, monkeypatch):
        """If the API only returns coverage for one KB, others default to 0."""
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=200, json_body={"support": 1.0})
        cache = _StubCache()

        result = await hook._fetch_taxonomy_coverage(
            "org-1", ["support", "billing"], cache, _transport=transport
        )

        assert result == {"support": 1.0, "billing": 0.0}

    @pytest.mark.asyncio
    async def test_returns_zeros_on_error(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=500, json_body={"detail": "boom"})
        cache = _StubCache()

        result = await hook._fetch_taxonomy_coverage(
            "org-1", ["support", "billing"], cache, _transport=transport
        )

        assert result == {"support": 0.0, "billing": 0.0}

    @pytest.mark.asyncio
    async def test_returns_zeros_when_disabled(self, monkeypatch):
        hook = _load_hook(monkeypatch, extra_env={"TAXONOMY_ENABLED": "false"})
        cache = _StubCache()

        result = await hook._fetch_taxonomy_coverage("org-1", ["support"], cache)

        assert result == {"support": 0.0}

    @pytest.mark.asyncio
    async def test_redis_cache_hit_short_circuits_request(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        transport = _MockTransport(status_code=200, json_body={"unused": 1.0})
        cache = _StubCache()
        cache.store["tax_coverage:org-1:billing,support"] = {
            "support": 1.0,
            "billing": 0.0,
        }

        result = await hook._fetch_taxonomy_coverage(
            "org-1", ["support", "billing"], cache, _transport=transport
        )

        assert result == {"support": 1.0, "billing": 0.0}
        assert transport.requests == []


# ---------------------------------------------------------------------------
# _format_taxonomy_for_prompt (multi-KB + legacy)
# ---------------------------------------------------------------------------


class TestFormatTaxonomyForPrompt:
    def test_legacy_list_renders_flat_lines(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        result = hook._format_taxonomy_for_prompt(_TREE_LEGACY)
        assert "id=1: SSO" in result
        assert "id=2: SAML" in result

    def test_multi_kb_dict_renders_kb_labels(self, monkeypatch):
        """Multi-KB shape MUST surface the KB context to the LLM."""
        hook = _load_hook(monkeypatch)
        result = hook._format_taxonomy_for_prompt(_TREES_MULTI)
        # KB context labels.
        assert "[support]" in result
        assert "[billing]" in result
        # Nodes from each KB.
        assert "id=1: SSO" in result
        assert "id=10: Invoices" in result

    def test_returns_none_for_empty_legacy(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        assert hook._format_taxonomy_for_prompt([]) == "(none)"

    def test_returns_none_for_empty_dict(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        assert hook._format_taxonomy_for_prompt({}) == "(none)"

    def test_truncates_at_max_nodes_per_kb(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        big = {
            "kb1": [
                {"id": i, "kb_slug": "kb1", "name": f"Node {i}", "parent_id": None}
                for i in range(50)
            ]
        }

        result = hook._format_taxonomy_for_prompt(big, max_nodes_per_kb=10)

        node_lines = [ln for ln in result.splitlines() if "id=" in ln]
        assert len(node_lines) == 10
        assert "more nodes omitted" in result


# ---------------------------------------------------------------------------
# _flatten_trees
# ---------------------------------------------------------------------------


class TestFlattenTrees:
    def test_flattens_dict_to_single_list(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        flat = hook._flatten_trees(_TREES_MULTI)
        ids = {n["id"] for n in flat}
        assert ids == {1, 2, 10, 11}

    def test_passthrough_for_legacy_list(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        flat = hook._flatten_trees(_TREE_LEGACY)
        assert flat == _TREE_LEGACY

    def test_empty_dict_returns_empty_list(self, monkeypatch):
        hook = _load_hook(monkeypatch)
        assert hook._flatten_trees({}) == []
