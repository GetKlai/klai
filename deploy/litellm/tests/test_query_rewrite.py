"""SPEC-RAG-QUERY-REWRITE-001 — _rewrite_query helper unit tests.

litellm is not installed locally (runs in Docker), so we mock the import via
the shared fixture in test_klai_knowledge_hook.py.
"""

import asyncio
import importlib
import sys
import types

import httpx
import pytest

from tests.klai_module_reset import reset_klai_kb_modules


@pytest.fixture(autouse=True)
def _mock_litellm():
    """Mock litellm module so klai_knowledge can be imported."""
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


def _load_hook(monkeypatch, extra_env=None):
    env = {
        "PORTAL_INTERNAL_SECRET": "test-portal-secret",
        "RETRIEVAL_INTERNAL_SECRET": "test-retrieval-secret",
        "KNOWLEDGE_RETRIEVE_URL": "http://retrieval-api:8040/retrieve",
        "PORTAL_API_URL": "http://portal-api:8000",
        "LITELLM_MASTER_KEY": "test-litellm-key",
    }
    if extra_env:
        env.update(extra_env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_klai_kb_modules()
    import klai_knowledge

    importlib.reload(klai_knowledge)
    return klai_knowledge


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, status_code: int, json_body: dict | None = None) -> None:
        self._status_code = status_code
        self._json_body = json_body or {}
        self.request: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import json

        self.request = request
        return httpx.Response(
            status_code=self._status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(self._json_body).encode(),
            request=request,
        )


def _ok_response(content: str) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 80, "completion_tokens": 25},
    }


_HISTORY_3_TURNS = [
    {"role": "user", "content": "Hoe gaat het met de portering van klant Jansen B.V.?"},
    {
        "role": "assistant",
        "content": "De uitportering van Jansen B.V. wacht op bevestiging van KPN.",
    },
    {
        "role": "user",
        "content": "Wat is de status van de aanvraag?",
    },
]


# ---------------------------------------------------------------------------
# Skip-conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_query_skips_when_no_history(monkeypatch):
    hook = _load_hook(monkeypatch)
    rewritten, meta = await hook._rewrite_query("Wat zei hij?", [])
    assert rewritten == "Wat zei hij?"
    assert meta["skipped"] == "no_history"
    assert meta["was_changed"] is False


@pytest.mark.asyncio
async def test_rewrite_query_skips_when_disabled(monkeypatch):
    hook = _load_hook(monkeypatch, extra_env={"QUERY_REWRITE_ENABLED": "false"})
    rewritten, meta = await hook._rewrite_query("Wat zei hij?", _HISTORY_3_TURNS)
    assert rewritten == "Wat zei hij?"
    assert meta["skipped"] == "disabled"


@pytest.mark.asyncio
async def test_rewrite_query_skips_when_no_api_key(monkeypatch):
    hook = _load_hook(monkeypatch, extra_env={"LITELLM_MASTER_KEY": ""})
    rewritten, meta = await hook._rewrite_query("Wat zei hij?", _HISTORY_3_TURNS)
    assert rewritten == "Wat zei hij?"
    assert meta["skipped"] == "no_api_key"


@pytest.mark.asyncio
async def test_rewrite_query_skips_on_empty_query(monkeypatch):
    hook = _load_hook(monkeypatch)
    rewritten, meta = await hook._rewrite_query("", _HISTORY_3_TURNS)
    assert rewritten == ""
    assert meta["skipped"] == "empty_query"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_query_returns_rewritten_string_on_200(monkeypatch):
    hook = _load_hook(monkeypatch)
    rewritten_content = "Wat is de status van de portering-aanvraag van Jansen B.V.?"
    transport = _MockTransport(
        status_code=200, json_body=_ok_response(rewritten_content)
    )

    rewritten, meta = await hook._rewrite_query(
        "Wat is de status van de aanvraag?",
        _HISTORY_3_TURNS,
        _transport=transport,
    )

    assert rewritten == rewritten_content
    assert meta["was_changed"] is True
    assert meta["rewrite_ms"] >= 0
    assert "skipped" not in meta


@pytest.mark.asyncio
async def test_rewrite_query_strips_surrounding_quotes(monkeypatch):
    """Mistral occasionally wraps the rewrite in quotes — strip them off."""
    hook = _load_hook(monkeypatch)
    transport = _MockTransport(
        status_code=200,
        json_body=_ok_response('"Wat is de status van de portering van Jansen B.V.?"'),
    )

    rewritten, meta = await hook._rewrite_query(
        "Wat is de status van de aanvraag?",
        _HISTORY_3_TURNS,
        _transport=transport,
    )

    assert not rewritten.startswith('"')
    assert not rewritten.endswith('"')
    assert "Jansen" in rewritten


@pytest.mark.asyncio
async def test_rewrite_query_was_changed_false_when_identical(monkeypatch):
    """If the model returns the input unchanged, was_changed is False."""
    hook = _load_hook(monkeypatch)
    raw = "Hoe troubleshoot ik Bubble?"
    transport = _MockTransport(status_code=200, json_body=_ok_response(raw))

    rewritten, meta = await hook._rewrite_query(
        raw, _HISTORY_3_TURNS, _transport=transport
    )

    assert rewritten == raw
    assert meta["was_changed"] is False


@pytest.mark.asyncio
async def test_rewrite_query_rejects_destructive_rewrite(monkeypatch):
    """A self-contained query must not be rewritten to an unrelated history topic."""
    hook = _load_hook(monkeypatch)
    raw = "Wat weet je over klai?"
    transport = _MockTransport(
        status_code=200,
        json_body=_ok_response(
            "Hoe stel ik een Yealink toestel in en welke instellingen zijn er mogelijk?"
        ),
    )

    rewritten, meta = await hook._rewrite_query(
        raw,
        _HISTORY_3_TURNS,
        _transport=transport,
    )

    assert rewritten == raw
    assert meta["skipped"] == "destructive_rewrite"
    assert meta["was_changed"] is False
    assert meta["dropped_salient_tokens"] == ["klai"]


def test_rewrite_decided_semantics(monkeypatch):
    """``rewrite_decided`` drives the /retrieve ``coreference_resolved`` field.

    True when the rewrite pipeline made the coreference decision (successful
    rewrite OR guard fallback to raw); False on infrastructure skips so
    retrieval-api may run its own resolver as fallback. The guard-fire case is
    the regression: without an explicit True, ``raw_query == query`` made
    retrieval-api re-run an unguarded rewrite of the exact query the guard
    just protected (2026-07-09 incident class).
    """
    hook = _load_hook(monkeypatch)

    assert hook._rewrite_decided({}) is True  # successful rewrite, no skip
    assert hook._rewrite_decided({"skipped": "destructive_rewrite"}) is True
    for infra_skip in (
        "disabled",
        "no_api_key",
        "exception",
        "empty_response",
        "empty_rewritten_query",
        "no_history",
        "empty_query",
    ):
        assert hook._rewrite_decided({"skipped": infra_skip}) is False, infra_skip


@pytest.mark.asyncio
async def test_rewrite_query_allows_followup_resolution(monkeypatch):
    """Follow-up queries may pull missing subject context from history."""
    hook = _load_hook(monkeypatch)
    rewritten_content = "Wat is de status van de portering-aanvraag van Jansen B.V.?"
    transport = _MockTransport(
        status_code=200, json_body=_ok_response(rewritten_content)
    )

    rewritten, meta = await hook._rewrite_query(
        "Wat is de status van de aanvraag?",
        _HISTORY_3_TURNS,
        _transport=transport,
    )

    assert rewritten == rewritten_content
    assert meta["was_changed"] is True
    assert "skipped" not in meta


# ---------------------------------------------------------------------------
# Failure modes — all fall back to raw_query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_query_falls_back_on_500(monkeypatch):
    hook = _load_hook(monkeypatch)
    transport = _MockTransport(status_code=500, json_body={"detail": "boom"})

    rewritten, meta = await hook._rewrite_query(
        "Wat zei hij?", _HISTORY_3_TURNS, _transport=transport
    )

    assert rewritten == "Wat zei hij?"
    assert meta["skipped"] == "exception"
    assert "error" in meta


@pytest.mark.asyncio
async def test_rewrite_query_falls_back_on_empty_response(monkeypatch):
    hook = _load_hook(monkeypatch)
    transport = _MockTransport(status_code=200, json_body=_ok_response(""))

    rewritten, meta = await hook._rewrite_query(
        "Wat zei hij?", _HISTORY_3_TURNS, _transport=transport
    )

    assert rewritten == "Wat zei hij?"
    assert meta["skipped"] == "empty_response"


# ---------------------------------------------------------------------------
# History formatting
# ---------------------------------------------------------------------------


def test_format_history_truncates_to_max_chars(monkeypatch):
    hook = _load_hook(monkeypatch)
    long_history = [
        {"role": "user", "content": "x" * 600},
        {"role": "assistant", "content": "y" * 600},
    ]
    formatted = hook._format_history_for_rewrite(long_history, max_chars=300)
    assert len(formatted) <= 320  # 300 + ellipsis + role prefix slack
    assert "…" in formatted


def test_format_history_skips_blank_content(monkeypatch):
    hook = _load_hook(monkeypatch)
    history = [
        {"role": "user", "content": "Real question"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "  "},
        {"role": "assistant", "content": "Real answer"},
    ]
    formatted = hook._format_history_for_rewrite(history)
    assert "Real question" in formatted
    assert "Real answer" in formatted
    # Two blanks dropped:
    assert formatted.count("USER:") == 1
    assert formatted.count("ASSISTANT:") == 1


# ---------------------------------------------------------------------------
# SPEC-RAG-CORRESPONDENCE-DISTILL-001 — pasted-correspondence distillation
# ---------------------------------------------------------------------------

# Exact ground-truth of _QUERY_REWRITE_PROMPT as it existed before this SPEC
# (captured verbatim from the pre-change module). AC-3: the formatted prompt
# sent to the LLM MUST be byte-identical to this when pasted_correspondence
# is False (the default) — proves zero behavior change for ordinary turns.
_PRE_SPEC_PLAIN_PROMPT_TEMPLATE = (
    "You are a query rewriter for a RAG search system. Rewrite the user's "
    "current question so it makes sense as a stand-alone search query — "
    "resolve pronouns and references using the conversation history. If the "
    "question is already clear and self-contained, return it unchanged.\n\n"
    "The rewrite MUST keep the subject of the user's CURRENT question. "
    "History may only supply referents for pronouns, ellipsis, or follow-up "
    "phrases — never replace the current question's topic with a topic from "
    "history. When the current question introduces a new topic, ignore the "
    "history and return the question unchanged.\n\n"
    "Brand-bridging: if the question mentions a third-party brand or product "
    "name (e.g. Salesforce, HubSpot, Pipedrive, Zoom, Microsoft Teams, "
    "Outlook), also include 2–4 broader category or related-brand terms in "
    "the rewritten query so search can find category-specific or partner-brand "
    "pages even when the original brand string is absent. If no third-party "
    "brand is mentioned, leave the rewrite unchanged beyond standard pronoun "
    "resolution.\n\n"
    "Conversation history (oldest → newest):\n{history}\n\n"
    "User's current question: {raw_query}\n\n"
    "Reply with ONLY the rewritten question, no preamble, no explanation, "
    "no quotes. Maximum 200 characters. Same language as the user's input."
)


class _CapturingTransport(httpx.AsyncBaseTransport):
    """Like _MockTransport but records the outbound request body."""

    def __init__(self, status_code: int, json_body: dict | None = None) -> None:
        self._status_code = status_code
        self._json_body = json_body or {}
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import json

        self.requests.append(request)
        return httpx.Response(
            status_code=self._status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(self._json_body).encode(),
            request=request,
        )

    def sent_prompt(self) -> str:
        import json

        body = json.loads(self.requests[0].content)
        return body["messages"][0]["content"]


_PASTED_EMAIL_QUERY = (
    "Wat denk jij dat er niet goed is?\n\n"
    "Van: Klant <klant@example.nl>\n"
    "Verzonden: vrijdag 14 augustus 2026 21:22\n"
    "Aan: Support <support@example.nl>\n"
    "Onderwerp: RE: storing URGENT\n\n"
    "Uitgaand bellen faalt met SIP 404 Not Found na een geslaagde sessie-opzet."
)


@pytest.mark.asyncio
async def test_rewrite_query_prompt_unchanged_when_no_correspondence(monkeypatch):
    """AC-3: pasted_correspondence=False (default) sends the byte-identical
    pre-SPEC prompt — zero behavior change for ordinary turns."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200, json_body=_ok_response("Wat is de status van de aanvraag?")
    )

    await hook._rewrite_query(
        "Wat is de status van de aanvraag?",
        _HISTORY_3_TURNS,
        _transport=transport,
    )

    history_str = hook._format_history_for_rewrite(_HISTORY_3_TURNS)
    expected = _PRE_SPEC_PLAIN_PROMPT_TEMPLATE.format(
        history=history_str, raw_query="Wat is de status van de aanvraag?"
    )
    assert transport.sent_prompt() == expected


@pytest.mark.asyncio
async def test_rewrite_query_prompt_includes_distillation_instructions_when_flagged(
    monkeypatch,
):
    """REQ-2: pasted_correspondence=True adds a distillation instruction
    block; the base prompt content is otherwise unchanged."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response("SIP 404 Not Found uitgaand bellen na sessie-opzet"),
    )

    await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    sent = transport.sent_prompt()
    assert "distill" in sent.lower()
    assert "verbatim" in sent.lower()
    # Original instructions still present — this is an addition, not a
    # replacement.
    assert "Brand-bridging" in sent
    assert "Reply with ONLY the rewritten question" in sent


@pytest.mark.asyncio
async def test_rewrite_query_distillation_preserves_technical_token(monkeypatch):
    """AC-5: a distilled query must keep at least one verbatim technical
    token from the source — also exercises REQ-4's existing guard, since a
    distillation that drops it would be rejected as destructive."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response("SIP 404 Not Found trunk uitgaand bellen"),
    )

    rewritten, meta = await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    assert "404" in rewritten
    assert meta.get("skipped") != "destructive_rewrite"


@pytest.mark.asyncio
async def test_rewrite_query_distillation_guard_still_rejects_lossy_rewrite(
    monkeypatch,
):
    """AC-4: the pre-existing destructive-rewrite guard (REQ-4) is not
    bypassed by pasted_correspondence=True — a distillation that drops every
    salient token from the source is rejected exactly like today."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response("Hoe stel ik een Yealink toestel in?"),
    )

    rewritten, meta = await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    assert rewritten == _PASTED_EMAIL_QUERY
    assert meta["skipped"] == "destructive_rewrite"


@pytest.mark.asyncio
async def test_rewrite_query_meta_carries_pasted_correspondence_flag(monkeypatch):
    """REQ-5: telemetry visibility, independent of skip/success outcome."""
    hook = _load_hook(monkeypatch)

    # Success path.
    transport = _CapturingTransport(
        status_code=200, json_body=_ok_response("SIP 404 trunk uitgaand bellen")
    )
    _, meta_true = await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )
    assert meta_true["pasted_correspondence_detected"] is True

    _, meta_default = await hook._rewrite_query(
        "Wat is het beleid voor opnames?", _HISTORY_3_TURNS, _transport=transport
    )
    assert meta_default["pasted_correspondence_detected"] is False

    # Skip path (disabled) — flag still recorded.
    hook_disabled = _load_hook(monkeypatch, extra_env={"QUERY_REWRITE_ENABLED": "false"})
    _, meta_skipped = await hook_disabled._rewrite_query(
        _PASTED_EMAIL_QUERY, [], allow_empty_history=True, pasted_correspondence=True
    )
    assert meta_skipped["pasted_correspondence_detected"] is True
    assert meta_skipped["skipped"] == "disabled"


@pytest.mark.asyncio
async def test_rewrite_query_distillation_excludes_unique_incident_identifiers(
    monkeypatch,
):
    """Empirical finding (this SPEC's AC-2 replay, 2026-08-18): preserving
    unique per-incident identifiers (Call-ID, specific trunk/account
    numbers) in the distilled query measurably HURTS retrieval — those
    tokens never appear in knowledge-base articles and pull the embedding
    away from the general topic (0.571 top score with identifiers vs. 0.847
    without, same underlying question, live retrieval-api A/B). The
    instruction must explicitly exclude them, distinct from the "preserve
    domain terminology verbatim" instruction for reusable technical terms."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response("SIP 404 Not Found uitgaand bellen na sessie-opzet"),
    )

    await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    sent = transport.sent_prompt().lower()
    assert "call-id" in sent
    assert "do not preserve" in sent or "not preserve" in sent


@pytest.mark.asyncio
async def test_rewrite_query_distillation_requests_keyword_style_output(monkeypatch):
    """Second empirical finding (same AC-2 replay session): a full
    grammatical question ('Wat veroorzaakt de 404 Not Found ... na
    succesvolle sessie-opzet?') scored WORSE (0.261, band=low, target
    article absent from top-5) than a terse keyword-style phrase ('SIP 404
    Not Found response code oorzaak', 0.974, band=high). The instruction
    must steer the model toward search-engine-style keyword phrasing, not
    natural-language questions."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response("SIP 404 Not Found uitgaand bellen na sessie-opzet"),
    )

    await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    sent = transport.sent_prompt().lower()
    assert "keyword" in sent
    assert "not a full grammatical question" in sent or "no question words" in sent


@pytest.mark.asyncio
async def test_rewrite_query_strips_markdown_from_distillation(monkeypatch):
    """Empirical finding (SPEC HISTORY 0.2.0): the model does not reliably
    follow the 'no markdown formatting' instruction. Enforce it in code —
    deterministic, not a request — for the distillation path only."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response(
            "Wat veroorzaakt de **404 Not Found** met `Q.850;cause=1` bij SIP?"
        ),
    )

    rewritten, _meta = await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    assert "*" not in rewritten
    assert "`" not in rewritten
    assert "404 Not Found" in rewritten


@pytest.mark.asyncio
async def test_rewrite_query_strips_long_digit_runs_from_distillation(monkeypatch):
    """Empirical finding (SPEC HISTORY 0.2.0): the model kept a 9-digit
    trunk number despite being told not to preserve unique per-incident
    identifiers — measurably hurt retrieval (0.571 vs 0.847 top score, live
    A/B). SIP/HTTP status codes are always 3 digits; any 5+ digit run is
    almost certainly a unique identifier. Enforce dropping it in code."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response(
            "404 Not Found trunk 451030015 uitgaand bellen sessie-opzet"
        ),
    )

    rewritten, _meta = await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    assert "451030015" not in rewritten
    assert "404" in rewritten
    assert "trunk" in rewritten


@pytest.mark.asyncio
async def test_rewrite_query_cleanup_does_not_apply_without_correspondence_flag(
    monkeypatch,
):
    """AC-3-adjacent regression guard: an ordinary (non-correspondence)
    rewrite must NOT be touched by the new cleanup step, even if it happens
    to contain markdown or a long number — e.g. a legitimate order number
    the user is asking about."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response("Status van bestelling **123456789**?"),
    )

    rewritten, _meta = await hook._rewrite_query(
        "Wat is de status van mijn bestelling?",
        _HISTORY_3_TURNS,
        _transport=transport,
    )

    assert rewritten == "Status van bestelling **123456789**?"


@pytest.mark.asyncio
async def test_rewrite_query_cleanup_skipped_when_guard_rejects(monkeypatch):
    """Cleanup must not run on the raw-fallback path (destructive-rewrite
    guard rejection) — that path returns the user's own pasted text
    unchanged, which must not be silently mangled."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response("Hoe stel ik een **Yealink** toestel in?"),
    )

    rewritten, meta = await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    assert meta["skipped"] == "destructive_rewrite"
    assert rewritten == _PASTED_EMAIL_QUERY


# ---------------------------------------------------------------------------
# Central LiteLLM rewrite routing
# ---------------------------------------------------------------------------
#
# Query rewrite must use the proxy's existing RPM/TPM accounting and model
# fallback instead of maintaining a second, request-only provider limiter.


@pytest.mark.asyncio
async def test_rewrite_query_uses_proxy_quota_and_internal_bypass(monkeypatch):
    hook = _load_hook(monkeypatch)
    transport = _MockTransport(
        status_code=200, json_body=_ok_response("Wat is de status?")
    )

    await hook._rewrite_query(
        "Wat is de status van de aanvraag?",
        _HISTORY_3_TURNS,
        _transport=transport,
    )

    assert transport.request is not None
    assert str(transport.request.url) == "http://127.0.0.1:4000/v1/chat/completions"
    assert transport.request.headers["authorization"] == "Bearer test-litellm-key"
    payload = __import__("json").loads(transport.request.content)
    assert payload["model"] == "klai-fast"
    assert payload["metadata"]["_klai_openai_passthrough"] is True


@pytest.mark.asyncio
async def test_rewrite_and_classify_plain_fallback_uses_same_proxy_contract(monkeypatch):
    hook = _load_hook(monkeypatch)
    transport = _MockTransport(
        status_code=200, json_body=_ok_response("Wat is SAML?")
    )

    await hook._rewrite_and_classify(
        "Wat is SAML?", [], {}, _transport=transport
    )

    assert transport.request is not None
    payload = __import__("json").loads(transport.request.content)
    assert payload["model"] == "klai-fast"
    assert payload["metadata"]["_klai_openai_passthrough"] is True


# ---------------------------------------------------------------------------
# Review #7 — underscore preserved, code-context digit runs preserved
# ---------------------------------------------------------------------------

_PASTED_EMAIL_QUERY_ERR = (
    "Wat is hier het probleem?\n\n"
    "Van: Klant <klant@example.nl>\n"
    "Verzonden: vrijdag 14 augustus 2026 21:22\n"
    "Aan: Support <support@example.nl>\n"
    "Onderwerp: RE: login storing URGENT\n\n"
    "Inloggen faalt met ERR_AUTH_FAILED na correcte invoer van het wachtwoord."
)


@pytest.mark.asyncio
async def test_rewrite_query_preserves_underscore_identifier_in_distillation(
    monkeypatch,
):
    """HISTORY 0.4.0 / review finding #7: underscore is no longer stripped by
    the cleanup regex — it is the word-separator in reusable identifiers like
    ERR_AUTH_FAILED, not markdown emphasis. The pre-fix `[*_\\`]` pattern
    mangled these into 'ERRAUTHFAILED', directly contradicting the
    'preserve error codes verbatim' requirement."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response(
            "ERR_AUTH_FAILED bij inloggen na wachtwoord invoer"
        ),
    )

    rewritten, meta = await hook._rewrite_query(
        _PASTED_EMAIL_QUERY_ERR,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    assert "ERR_AUTH_FAILED" in rewritten
    assert meta.get("skipped") != "destructive_rewrite"


@pytest.mark.asyncio
async def test_rewrite_query_distillation_preserves_code_context_digit_runs(
    monkeypatch,
):
    """review finding #7: a 5+ digit run immediately preceded by a hyphen
    (structured code like CVE-2026-12345) or preceded within ~20 chars by a
    code/error/status/cve keyword (e.g. "error 10060") survives cleanup —
    these are reusable technical identifiers, not unique per-incident
    numbers."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response(
            "CVE-2026-12345 error 10060 SIP 404 verbinding mislukt"
        ),
    )

    rewritten, meta = await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    assert "CVE-2026-12345" in rewritten
    assert "10060" in rewritten
    assert meta.get("skipped") != "destructive_rewrite"


@pytest.mark.asyncio
async def test_rewrite_query_distillation_strips_digit_runs_without_code_context(
    monkeypatch,
):
    """review finding #7 counterpart: a bare digit run with no hyphen/keyword
    context — a phone number or trunk/ticket number — is still stripped,
    exactly like before this fix."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response(
            "Bel 0612345678 over trunk 202392 SIP 404 uitgaand bellen"
        ),
    )

    rewritten, meta = await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    assert "0612345678" not in rewritten
    assert "202392" not in rewritten
    assert "404" in rewritten
    assert meta.get("skipped") != "destructive_rewrite"


# ---------------------------------------------------------------------------
# Review #8 — clean-then-guard ordering regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_query_cleanup_before_guard_never_returns_empty_query(
    monkeypatch,
):
    """review finding #8: the model rewrite overlaps raw_query ONLY on the
    exact identifier that cleanup then strips (raw "Ticket 123456" vs. model
    output "123456"). Cleaning BEFORE the destructive-rewrite guard means the
    guard's own salient-token-overlap check naturally rejects this case,
    instead of letting an emptied string through to retrieval."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200, json_body=_ok_response("123456")
    )

    rewritten, meta = await hook._rewrite_query(
        "Ticket 123456",
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    assert rewritten == "Ticket 123456"
    assert rewritten.strip() != ""
    assert meta["skipped"] in ("destructive_rewrite", "empty_after_distillation")


# ---------------------------------------------------------------------------
# Total proxy call bounded by QUERY_REWRITE_TIMEOUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_query_total_proxy_timeout_falls_back(monkeypatch):
    hook = _load_hook(monkeypatch)
    query_rewrite_module = sys.modules["klai_kb_query_rewrite"]
    monkeypatch.setattr(query_rewrite_module, "QUERY_REWRITE_TIMEOUT", 0.05)

    class SlowTransport(_MockTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(0.2)
            return await super().handle_async_request(request)

    transport = SlowTransport(status_code=200, json_body=_ok_response("Wat is de status?"))

    rewritten, meta = await asyncio.wait_for(
        hook._rewrite_query(
            "Wat is de status van de aanvraag?",
            _HISTORY_3_TURNS,
            _transport=transport,
        ),
        timeout=1.0,
    )

    assert rewritten == "Wat is de status van de aanvraag?"
    assert meta["skipped"] == "exception"


# ---------------------------------------------------------------------------
# Sol delta-review Fix 2 — deterministic SIP Call-ID / IPv4 stripping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_query_strips_sip_call_id_and_ipv4_from_distillation(
    monkeypatch,
):
    """A distilled output that keeps a SIP Call-ID (token@host shape) and/or
    a raw IPv4 address leaks unique per-incident identifiers into the
    retrieval query, same failure class as long digit runs and phone
    numbers. Both must be stripped deterministically; the reusable "SIP 404
    Not Found trunk" vocabulary around them must survive untouched."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response("Call-ID aa11bb22@203.0.113.42 SIP 404 Not Found trunk"),
    )

    rewritten, meta = await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    assert "aa11bb22@203.0.113.42" not in rewritten
    assert "203.0.113.42" not in rewritten
    assert "SIP 404 Not Found trunk" in rewritten
    assert meta.get("skipped") != "destructive_rewrite"


# ---------------------------------------------------------------------------
# Sol delta-review Fix 3 — narrow the hyphen exception in the digit-run rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_query_digit_run_hyphen_exception_narrowed_to_uppercase_codes(
    monkeypatch,
):
    """The old bare `prefix.endswith("-")` check preserved ANY hyphenated
    digit run, including lowercase incident identifiers like
    "ticket-123456" and "trunk-451030015" — exactly the class of unique
    per-incident identifier this cleanup exists to strip. Only an
    uppercase structured-code shape ending in a hyphen (CVE-2026-12345,
    ERR-10060) should survive; plain lowercase-prefixed digit runs must
    now be stripped like any other bare digit run."""
    hook = _load_hook(monkeypatch)
    transport = _CapturingTransport(
        status_code=200,
        json_body=_ok_response(
            "CVE-2026-12345 ERR-10060 ticket-123456 trunk-451030015 06-12345678 SIP 404"
        ),
    )

    rewritten, meta = await hook._rewrite_query(
        _PASTED_EMAIL_QUERY,
        [],
        allow_empty_history=True,
        pasted_correspondence=True,
        _transport=transport,
    )

    assert "CVE-2026-12345" in rewritten
    assert "ERR-10060" in rewritten
    assert "ticket-123456" not in rewritten
    assert "123456" not in rewritten
    assert "trunk-451030015" not in rewritten
    assert "451030015" not in rewritten
    assert "06-12345678" not in rewritten
    assert "12345678" not in rewritten
    assert meta.get("skipped") != "destructive_rewrite"
