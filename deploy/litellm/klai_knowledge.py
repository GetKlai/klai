"""
KlaiKnowledgeHook — LiteLLM pre-call hook that enriches LibreChat messages
with relevant organizational knowledge from the Klai Knowledge Service.

Mount into LiteLLM container at /app/custom/ and set PYTHONPATH=/app/custom.
Configure in config.yaml:
  litellm_settings:
    callbacks:
      - klai_knowledge.klai_knowledge_hook

Authorization is fail-closed: any user without a verified knowledge entitlement
receives no KB injection. If the portal authorization endpoint is unreachable,
injection is silently skipped (WARNING logged).

KB-context presence is signalled to downstream hooks via data["_klai_kb_meta"].
The custom_router uses this to prevent model downgrade for KB-enriched requests.
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

# SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): the language-detection
# foundation that this hook prepends to every LibreChat system message,
# matching what synthesis.py (path C) and partner_chat.py (path B) already
# do. Imported from the vendored single-file copy at
# deploy/litellm/klai_chat_prompts.py — see that file's docstring for the
# vendoring rationale (mirrors klai_service_auth.py from Phase C-1). Drift
# vs the canonical klai-libs/chat-prompts is enforced by
# deploy/litellm/tests/test_klai_chat_prompts_drift.py.
#
# Note (re-introduced 2026-05-07 after the inline-hotfix detour): the
# original Phase 4 commit imported this and broke production because the
# old ``Deploy LiteLLM hooks`` workflow used ``docker compose restart
# litellm``, which silently ignores new bind-mounts. The cleanup PR that
# restored this import ALSO switched the workflow to
# ``/opt/klai/scripts/compose-up.sh litellm`` (= ``docker compose up -d
# --remove-orphans litellm``) so new mounts are picked up automatically —
# matching every other klai service deploy.
from klai_chat_prompts import (
    GENERAL_CHAT_SYSTEM_PROMPT,
    GROUNDED_CHAT_SYSTEM_PROMPT,
    META_CHAT_SYSTEM_PROMPT,
)
from klai_citations import (
    CitationRegistry,
    build_citation_registry,
    format_sources_markdown,
    render_evidence_context,
    render_markdown_answer,
    strip_model_citation_artifacts,
)

# SPEC-MCP-RETRIEVAL-001 Phase 1: telemetry helpers moved out of this file
# into ``klai-libs/retrieval-telemetry/`` so klai-knowledge-mcp's new
# ``search_knowledge`` tool can emit identical retrieval-log + gap-event
# payloads. The vendored single-file copy at
# ``deploy/litellm/klai_retrieval_telemetry.py`` mirrors the canonical
# ``klai-libs/retrieval-telemetry/klai_retrieval_telemetry/_emit.py``.
# Drift is enforced by ``test_klai_retrieval_telemetry_drift.py``.
#
# We bind to underscore-prefixed module locals to preserve the existing
# call sites (``_classify_gap``, ``_fire_gap_event``, ``_fire_retrieval_log``)
# without a touch-every-call-site refactor. ``caller_client_id`` defaults
# to ``None`` for all LibreChat-path emits, preserving the wire shape.
from klai_retrieval_telemetry import (
    classify_gap as _classify_gap,
)
from klai_retrieval_telemetry import (
    fire_gap_event as _fire_gap_event,
)
from klai_retrieval_telemetry import (
    fire_retrieval_log as _fire_retrieval_log,
)
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger(__name__)

KNOWLEDGE_RETRIEVE_URL = os.getenv("KNOWLEDGE_RETRIEVE_URL")
if not KNOWLEDGE_RETRIEVE_URL:
    raise RuntimeError("KNOWLEDGE_RETRIEVE_URL is not set")
PORTAL_API_URL = os.getenv("PORTAL_API_URL", "http://portal-api:8000")

# PORTAL_INTERNAL_SECRET authenticates calls to portal-api (entitlement check
# at /internal/v1/kb/feature, template fetch at /internal/templates/effective).
# Maps to PORTAL_API_INTERNAL_SECRET in SOPS / portal-api validates with that.
PORTAL_INTERNAL_SECRET = os.getenv("PORTAL_INTERNAL_SECRET", "")

# RETRIEVAL_INTERNAL_SECRET authenticates the /retrieve call on retrieval-api.
# retrieval-api validates against its own INTERNAL_SECRET env var (mapped from
# RETRIEVAL_API_INTERNAL_SECRET in SOPS) — a DIFFERENT secret from portal-api's.
# When unset (e.g. older deploys), falls back to PORTAL_INTERNAL_SECRET so the
# hook keeps shipping headers, but in production both secrets must be set or
# the legacy auth path on /retrieve 401s with `invalid_internal_secret`.
RETRIEVAL_INTERNAL_SECRET = (
    os.getenv("RETRIEVAL_INTERNAL_SECRET", "") or PORTAL_INTERNAL_SECRET
)

# SPEC-SEC-SERVICE-AUTH-001 Phase C-1: Zitadel client_credentials JWT auth
# replaces the shared X-Internal-Secret. The token client is constructed lazily
# the first time it is needed — that way a service still boots even if the
# Zitadel env vars are missing during the rollout (REQ-5: fall back to legacy).
KLAI_OAUTH_TOKEN_URL = os.getenv("KLAI_OAUTH_TOKEN_URL", "")
KLAI_LITELLM_CLIENT_ID = os.getenv("KLAI_LITELLM_CLIENT_ID", "")
KLAI_LITELLM_CLIENT_SECRET = os.getenv("KLAI_LITELLM_CLIENT_SECRET", "")

# Lazy-built ZitadelTokenClient instance. None when env vars are missing —
# the retrieve call site handles that by falling back to the legacy
# X-Internal-Secret path (Phase C-1 REQ-5 safe rollout).
_token_client: object | None = None
_token_client_init_attempted: bool = False


def _get_token_client() -> object | None:
    """Return the cached ``ZitadelTokenClient`` or ``None`` if unconfigured.

    Builds it on first call. Subsequent calls reuse the same instance.
    Returns ``None`` (not raises) when env vars are missing — callers are
    expected to fall back to the legacy auth path. This is intentional:
    Phase C-1 deploys the code BEFORE the operator finishes the Zitadel
    bootstrap, so a missing-config branch must keep chat working.
    """
    global _token_client, _token_client_init_attempted

    if _token_client is not None:
        return _token_client
    if _token_client_init_attempted:
        return None

    _token_client_init_attempted = True
    if not (
        KLAI_OAUTH_TOKEN_URL and KLAI_LITELLM_CLIENT_ID and KLAI_LITELLM_CLIENT_SECRET
    ):
        logger.info(
            "KlaiKnowledgeHook: jwt auth env vars missing — using legacy "
            "X-Internal-Secret path (SPEC-SEC-SERVICE-AUTH-001 Phase C-1 fallback)"
        )
        return None

    try:
        from klai_service_auth import SCOPE_RETRIEVAL_QUERY, ZitadelTokenClient

        _token_client = ZitadelTokenClient(
            client_id=KLAI_LITELLM_CLIENT_ID,
            client_secret=KLAI_LITELLM_CLIENT_SECRET,
            token_url=KLAI_OAUTH_TOKEN_URL,
            scope=SCOPE_RETRIEVAL_QUERY,
        )
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.info(
            "KlaiKnowledgeHook: zitadel token client initialised (oauth scope=%s)",
            SCOPE_RETRIEVAL_QUERY,
        )
        return _token_client
    except Exception as exc:
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.warning(
            "KlaiKnowledgeHook: zitadel token client init failed (%s) — "
            "falling back to legacy auth header",
            exc,
        )
        return None


async def _retrieve_jwt_headers() -> dict[str, str] | None:
    """Mint a Zitadel JWT and return ``Authorization: Bearer …``.

    Returns ``None`` when no token client is configured OR when minting
    fails. The caller then uses the legacy X-Internal-Secret path.

    SPEC-SEC-IDENTITY-ASSERT-001 REQ-4.2: ``X-Caller-Service`` header is
    required by retrieval-api for any /retrieve request that carries an
    end-user identity in the body. We always include it — both on the JWT
    and the legacy auth path — so a future Phase D cleanup that removes
    the legacy path doesn't quietly drop the header.
    """
    client = _get_token_client()
    if client is None:
        return None
    try:
        token = await client.get_token()  # type: ignore[attr-defined]
        return {
            "Authorization": f"Bearer {token}",
            "X-Caller-Service": "litellm",
        }
    except Exception as exc:
        # Mint failed (Zitadel down, bad creds, network error). Logged
        # by ZitadelTokenClient; we just record the fallback decision.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.warning(
            "KlaiKnowledgeHook: jwt mint failed (%s) — falling back to legacy auth header",
            exc,
        )
        return None


def _retrieve_legacy_headers() -> dict[str, str]:
    """Legacy X-Internal-Secret + X-Caller-Service header set for /retrieve.

    Uses RETRIEVAL_INTERNAL_SECRET (which falls back to PORTAL_INTERNAL_SECRET
    when unset). retrieval-api validates against its own INTERNAL_SECRET env
    var which is sourced from RETRIEVAL_API_INTERNAL_SECRET in SOPS — that is
    a DIFFERENT secret from portal-api's. Sending portal-api's secret here is
    the bug that historically caused `invalid_internal_secret` rejections on
    every retrieve call when the two secrets diverged.

    SPEC-SEC-IDENTITY-ASSERT-001 REQ-4.2: ``X-Caller-Service: litellm`` is
    REQUIRED — without it retrieval-api returns HTTP 400
    ``missing_caller_service`` and the hook silently degrades chat to
    "no KB". This was the regression that broke production for ~7 days
    after Phase D landed on 2026-04-28.
    """
    if not RETRIEVAL_INTERNAL_SECRET:
        return {}
    return {
        "X-Internal-Secret": RETRIEVAL_INTERNAL_SECRET,
        "X-Caller-Service": "litellm",
    }


async def _retrieve_with_dual_auth(
    http: httpx.AsyncClient, body: dict[str, Any]
) -> httpx.Response:
    """POST to ``/retrieve`` preferring JWT, falling back to X-Internal-Secret.

    Phase C-1 dual-auth (REQ-5 safe rollout):

    1. If a configured ``ZitadelTokenClient`` mints a token successfully,
       call ``/retrieve`` with ``Authorization: Bearer <jwt>``.
    2. If that call returns 200, we're done.
    3. If the call returns 401/403, the receiver's IdP setup (Zitadel
       project + audience + role grant) is incomplete — retry once with
       legacy ``X-Internal-Secret``. Logged so observability tracks the
       fallback rate, which must hit zero before Phase D cleanup.
    4. If JWT mint itself fails (no client configured, network error,
       bad creds), skip step 1-3 and call directly with X-Internal-Secret.

    The legacy fallback is removed in Phase D once retrieval-api's
    Zitadel project + audience config is finalized AND the fallback
    counter holds zero for the 7-day soak window.
    """
    jwt_headers = await _retrieve_jwt_headers()
    legacy_headers = _retrieve_legacy_headers()

    if jwt_headers is not None:
        resp = await http.post(KNOWLEDGE_RETRIEVE_URL, json=body, headers=jwt_headers)
        if resp.status_code not in (401, 403) or not legacy_headers:
            return resp
        # JWT was minted but receiver rejected the token. Most common cause
        # during Phase C-1 migration: receiver's audience/scope config not
        # yet wired up for this caller. Retry once with the legacy header.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.warning(
            "KlaiKnowledgeHook: jwt rejected by receiver (HTTP %d) — "
            "retrying with legacy auth header",
            resp.status_code,
        )

    return await http.post(KNOWLEDGE_RETRIEVE_URL, json=body, headers=legacy_headers)


RETRIEVE_TIMEOUT = float(os.getenv("KNOWLEDGE_RETRIEVE_TIMEOUT", "3.0"))
# SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-4: top_k raised from 5 to 20.
# Anthropic Contextual Retrieval finding ("top-20 chunks proved more
# effective than top-5 or top-10"). The reranker already produces 20
# candidates server-side (reranker_candidates=20) so this only changes
# how many chunks are forwarded to the LLM, not how many are scored.
RETRIEVE_TOP_K = int(os.getenv("KNOWLEDGE_RETRIEVE_TOP_K", "20"))
KLAI_GAP_SOFT_THRESHOLD = float(os.getenv("KLAI_GAP_SOFT_THRESHOLD", "0.4"))
KLAI_GAP_DENSE_THRESHOLD = float(os.getenv("KLAI_GAP_DENSE_THRESHOLD", "0.35"))
PORTAL_RETRIEVAL_LOG_URL = os.getenv(
    "PORTAL_RETRIEVAL_LOG_URL", f"{PORTAL_API_URL}/internal/v1/retrieval-log"
)
EMBEDDING_MODEL_VERSION = os.getenv("EMBEDDING_MODEL_VERSION", "bge-m3-v1")
KB_IMAGES_BASE_URL = os.getenv("KB_IMAGES_BASE_URL", "https://getklai.getklai.com")
_KB_RENDER_MODE_STREAMING_GUARD = "streaming_guard"
_KB_RENDER_MODE_LEGACY_STREAMING_GUARD = "legacy_stream_guard"
_KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING = "deterministic_non_streaming"
_KB_STREAMING_RENDER_MODES = {
    _KB_RENDER_MODE_STREAMING_GUARD,
    _KB_RENDER_MODE_LEGACY_STREAMING_GUARD,
}


@dataclass(frozen=True)
class KbCitationRenderStrategy:
    mode: str
    force_non_streaming: bool = False


def _resolve_kb_render_mode(value: object) -> str:
    """Resolve the configured citation rendering strategy.

    Streaming is the safe default for LibreChat/LangGraph. The old
    ``legacy_stream_guard`` value is accepted as an alias, but new metadata
    should use ``streaming_guard``.
    """
    requested = value.strip().lower() if isinstance(value, str) else ""
    if requested == _KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING:
        return _KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING
    return _KB_RENDER_MODE_STREAMING_GUARD


def _is_streaming_kb_render_mode(value: object) -> bool:
    return value in _KB_STREAMING_RENDER_MODES


def _select_kb_render_strategy(original_stream: object) -> KbCitationRenderStrategy:
    if original_stream is True:
        return KbCitationRenderStrategy(mode=_KB_RENDER_MODE_STREAMING_GUARD)
    if KLAI_KB_CHAT_RENDER_MODE == _KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING:
        return KbCitationRenderStrategy(
            mode=_KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING,
            force_non_streaming=True,
        )
    return KbCitationRenderStrategy(mode=_KB_RENDER_MODE_STREAMING_GUARD)


KLAI_KB_CHAT_RENDER_MODE = _resolve_kb_render_mode(os.getenv("KLAI_KB_CHAT_RENDER_MODE"))
_SENTINEL_URLS = {"undefined", "null", "none", "n/a", "na", "-", "#"}

# SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-2 — anti-hallucination injection
# fired when retrieval-api signals confidence_band ∈ {low, unknown}. Dutch
# baseline (Klai's primary user-facing language) — the model translates
# into the user's detected language via the existing LANGUAGE REMINDER.
# Tunable post-deploy by editing this constant + redeploying litellm; no
# retrieval-api change required.
_LOW_CONFIDENCE_INJECTION_TEXT = (
    "[Klai retrieval — lage relevantie]\n"
    "Het opgehaalde KB-materiaal heeft een lage relevantie-score voor "
    "deze vraag. Citeer alleen wat letterlijk in de chunks staat. Verzin "
    "GEEN integratie-routes, productnamen, stappen, bedragen, of "
    "technische details die niet expliciet in de chunks voorkomen. "
    "Sluit af met een vraag om verduidelijking aan de gebruiker als het "
    "materiaal de vraag niet volledig dekt — dat is beter dan een "
    "verzonnen antwoord."
)
_LOW_CONFIDENCE_INJECTION_DISABLED = (
    os.getenv("KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION", "0") == "1"
)

# SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-HOOK-U2: prompt-template fetch config.
PORTAL_TEMPLATES_URL = os.getenv(
    "PORTAL_TEMPLATES_URL", f"{PORTAL_API_URL}/internal/templates/effective"
)
TEMPLATES_TIMEOUT = float(os.getenv("TEMPLATES_TIMEOUT", "2.0"))

# Trivial message patterns — skip retrieval (NL + EN)
_TRIVIAL_PATTERNS = re.compile(
    r"^(ok|okay|oke|oké|ja|nee|yes|no|bedankt|thanks|thank you|"
    r"dank je|dank u|graag|np|prima|goed|good|sure|hmm+|ah+|oh+|"
    r"begrepen|understood|clear|got it|doei|bye|hoi|hallo|hello|hi)[\s!.?]*$",
    re.IGNORECASE,
)


def _is_trivial(text: str) -> bool:
    text = text.strip()
    if len(text) < 8:
        return True
    return bool(_TRIVIAL_PATTERNS.match(text))


# Meta-question patterns — detect questions ABOUT Klai itself (capability
# discovery) rather than questions about content in the knowledge base.
# Anchored at start AND end of string so we only match short stand-alone
# meta-questions, not content questions that happen to contain a meta-y
# substring (e.g. "hoe werkt onze prijsstrategie" must NOT match
# "hoe werkt"; "ik wil weten wat ik hier kan doen" must NOT match either).
#
# Background: 2026-05-12 Voys "Meldingen" incident. The user typed
# "wat kan ik hier?" — 16 chars, fell through `_is_trivial`, hit
# retrieval, returned a tangential KB chunk about voice-mail
# announcements ("Meldingen") that lexically matched on common Dutch
# words. The chunk became the answer. Solution: detect meta-questions
# before retrieval and route to META_CHAT_SYSTEM_PROMPT instead.
#
# Tight regex by design — preferring false negatives (a meta-question
# slipping through to retrieval, where it might still get a reasonable
# answer or a low-confidence abstention) over false positives (a
# legitimate content question losing access to its KB chunks).
_META_QUERY_PATTERNS = re.compile(
    r"^\s*(?:"
    # NL — "wat kan/kun ik/je/jij" + 0-3 modifier words ("hier", "doen",
    # "allemaal", "met klai/jou/je"). The {0,3} is what makes
    # "wat kan ik hier doen?" match alongside "wat kan ik?".
    r"wat\s+(?:kan|kun)\s+(?:ik|je|jij)"
    r"(?:\s+(?:hier|met\s+(?:klai|jou|je)|allemaal|doen)){0,3}"
    # NL — "wat is/doet/doe klai/jij/je"
    r"|wat\s+(?:is|doet|doe)\s+(?:klai|jij|je)"
    # NL — "wat kan/kun klai/je/jij (doen)?"
    r"|wat\s+(?:kan|kun)\s+(?:klai|je|jij)(?:\s+doen)?"
    # NL — "hoe werkt deze chat/dit/klai/jij/je". Longest alternatives
    # first so "deze chat" wins over "dit" in left-to-right alternation.
    r"|hoe\s+werkt\s+(?:deze\s+chat|dit|klai|jij|je)"
    # NL — "hoe gebruik/werk ik (met)? deze chat/klai/dit"
    r"|hoe\s+(?:gebruik|werk)\s+ik\s+(?:met\s+)?(?:deze\s+chat|klai|dit)"
    # NL — "wie ben je"
    r"|wie\s+ben\s+je"
    # NL — "waarvoor is dit/klai"
    r"|waarvoor\s+is\s+(?:dit|klai)"
    # NL — "waar is dit/klai voor"
    r"|waar\s+is\s+(?:dit|klai)\s+voor"
    # NL/EN — "help" standalone (single rule, case-insensitive flag)
    r"|help"
    # EN — "what can I/you do" + 0-2 modifier suffixes
    r"|what\s+can\s+(?:i|you)\s+do"
    r"(?:\s+(?:here|with\s+(?:klai|you))){0,2}"
    # EN — "what is/are/does klai (do)?"
    r"|what\s+(?:is|are|does)\s+klai(?:\s+do)?"
    # EN — "how does this chat/this/klai work". Longest alternative first.
    r"|how\s+does\s+(?:this\s+chat|this|klai)\s+work"
    # EN — "how do I use this chat/klai/this". Longest alternative first.
    r"|how\s+do\s+i\s+use\s+(?:this\s+chat|klai|this)"
    # EN — "who are you"
    r"|who\s+are\s+you"
    r")[\s!.?]*$",
    re.IGNORECASE,
)


def _is_meta_query(text: str) -> bool:
    """Return True if the user is asking ABOUT Klai itself (capability
    discovery) rather than asking a content question.

    Anchored full-string match — long content questions that contain
    a meta-y substring do not match. See ``_META_QUERY_PATTERNS`` for
    the rationale.
    """
    return bool(_META_QUERY_PATTERNS.match(text.strip()))


_TITLE_GENERATION_RE = re.compile(
    r"(?:"
    r"\b(?:generate|write|create|provide|give|summarize)\b"
    r"(?=[\s\S]{0,240}\b(?:title|name|summary)\b)"
    r"(?=[\s\S]{0,240}\b(?:conversation|chat)\b)"
    r"|\b(?:title|name)\s+(?:this|the)\s+(?:conversation|chat)\b"
    r")",
    re.IGNORECASE,
)


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _is_title_generation_request(messages: list[dict]) -> bool:
    """Detect LibreChat's internal conversation-title prompt.

    Title generation is application metadata, not a user KB question. Routing it
    through retrieval can turn the KB no-citation refusal into the conversation
    title itself.
    """
    for message in messages:
        if message.get("role") not in {"system", "developer", "user"}:
            continue
        text = _message_text(message)
        if not text or len(text) > 4000:
            continue
        if _TITLE_GENERATION_RE.search(text):
            return True
    return False


_WEB_SEARCH_TOOL_RE = re.compile(
    r"(?:^|[_\-\s])"
    r"(?:web[_\-\s]*search|websearch|search[_\-\s]*web|browser|searx|firecrawl)"
    r"(?:$|[_\-\s])",
    re.IGNORECASE,
)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _request_metadata(data: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for candidate in (
        data.get("metadata"),
        data.get("litellm_metadata"),
        data.get("litellm_params", {}).get("metadata")
        if isinstance(data.get("litellm_params"), dict)
        else None,
    ):
        if isinstance(candidate, dict):
            metadata.update(candidate)
    return metadata


def _tool_name(tool: object) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    names = [
        tool.get("name"),
        tool.get("type"),
        function.get("name") if isinstance(function, dict) else None,
    ]
    return " ".join(str(name) for name in names if name)


def _tool_description(tool: object) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    descriptions = [
        tool.get("description"),
        function.get("description") if isinstance(function, dict) else None,
    ]
    return " ".join(str(description) for description in descriptions if description)


def _request_has_web_search(data: dict[str, Any]) -> bool:
    """Return True when this LiteLLM request advertises Web Search.

    LibreChat can expose search either as an OpenAI-style tool/function or as
    explicit metadata. We accept both so the hook works with current LibreChat
    tool payloads and with a future first-class ``klai_web_search_enabled`` flag.
    """
    metadata = _request_metadata(data)
    for key in (
        "klai_web_search_enabled",
        "web_search_enabled",
        "webSearch",
        "web_search",
    ):
        if _truthy(metadata.get(key)):
            return True

    if isinstance(data.get("web_search_options"), dict):
        return True

    tools = data.get("tools")
    if not isinstance(tools, list):
        return False
    for tool in tools:
        name = _tool_name(tool)
        if name and _WEB_SEARCH_TOOL_RE.search(name):
            return True
        if (
            name.strip().lower() == "search"
            and "web" in _tool_description(tool).lower()
        ):
            return True
    return False


def _general_runtime_capabilities_block(data: dict[str, Any]) -> str:
    if not _request_has_web_search(data):
        return ""
    return (
        "[Klai Runtime Capabilities]\n"
        "Knowledge Base: none selected.\n"
        "Web Search: available for this turn.\n"
        "Instruction: for questions that need a live lookup, use the available "
        "Web Search tool or provided web results now. Do NOT tell the user to "
        "enable Search unless the tool call fails or no search result is returned.\n"
        "[End Klai Runtime Capabilities]"
    )


def _last_user_message(messages: list[dict]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Multi-modal message — extract text parts
                return " ".join(
                    p.get("text", "") for p in content if p.get("type") == "text"
                )
    return None


def _build_conversation_history(messages: list[dict]) -> list[dict]:
    """Return up to the last 6 turns (3 exchanges) of user/assistant history.

    The last user message is excluded — it is the current query being retrieved for.
    Used by retrieval-api for coreference resolution ("hij" → "Jan Pietersen").
    """
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in messages[:-1]
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ]
    return history[-6:]


# SPEC-RAG-QUERY-REWRITE-001: query rewriting for the /retrieve call.
#
# Why: vague follow-up queries like "Wat zei hij daarover?" carry no useful
# tokens for embedding match — pronouns and "die deal" only resolve via the
# previous turns. Rewriting them into self-contained queries closes the gap.
#
# Why direct Mistral (not through the LiteLLM proxy at localhost:4000):
# this code path runs INSIDE the proxy as a pre_call_hook callback.
# Recursing into the proxy would re-fire every callback (including this
# one) and can deadlock on rate-limited workers. A direct HTTPS call to
# api.mistral.ai sidesteps that and keeps the rewrite path free of
# bookkeeping side-effects (no extra LibreChat user-message log, no
# token-router second pass, no entitlement re-check).
QUERY_REWRITE_ENABLED = os.getenv("QUERY_REWRITE_ENABLED", "true").lower() == "true"
QUERY_REWRITE_TIMEOUT = float(os.getenv("QUERY_REWRITE_TIMEOUT", "1.5"))
QUERY_REWRITE_MODEL = os.getenv("QUERY_REWRITE_MODEL", "mistral-small-2603")
QUERY_REWRITE_HISTORY_TURNS = int(os.getenv("QUERY_REWRITE_HISTORY_TURNS", "4"))
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

# SPEC-RAG-TAXONOMY-001: Query-time taxonomy filtering
# KNOWLEDGE_RETRIEVE_URL already set above. Taxonomy endpoints live on the same
# retrieval-api base: /internal/v1/taxonomy/tree and /internal/v1/taxonomy/coverage.
_RETRIEVAL_BASE_URL = (KNOWLEDGE_RETRIEVE_URL or "").rsplit("/retrieve", 1)[0]
TAXONOMY_ENABLED = os.getenv("TAXONOMY_ENABLED", "true").lower() == "true"
# Coverage threshold: if < this fraction of KB chunks are tagged, skip filter.
# Default 0.30 — only skip when KB is mostly untagged (REQ-2 coverage-stats fallback).
KLAI_TAXONOMY_COVERAGE_THRESHOLD = float(
    os.getenv("KLAI_TAXONOMY_COVERAGE_THRESHOLD", "0.30")
)
# Timeout for each taxonomy HTTP call (tree fetch + coverage fetch).
TAXONOMY_FETCH_TIMEOUT = float(os.getenv("TAXONOMY_FETCH_TIMEOUT", "0.8"))

_QUERY_REWRITE_PROMPT = (
    "You are a query rewriter for a RAG search system. Rewrite the user's "
    "current question so it makes sense as a stand-alone search query — "
    "resolve pronouns and references using the conversation history. If the "
    "question is already clear and self-contained, return it unchanged.\n\n"
    "Conversation history (oldest → newest):\n{history}\n\n"
    "User's current question: {raw_query}\n\n"
    "Reply with ONLY the rewritten question, no preamble, no explanation, "
    "no quotes. Maximum 200 characters. Same language as the user's input."
)


def _format_history_for_rewrite(history: list[dict], max_chars: int = 1000) -> str:
    """Format the last N turns as plain "ROLE: content" lines, truncated."""
    if not history:
        return "(none)"
    tail = history[-QUERY_REWRITE_HISTORY_TURNS * 2 :]
    lines = []
    used = 0
    for turn in tail:
        role = turn.get("role", "?").upper()
        content = (turn.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        line = f"{role}: {content}"
        if used + len(line) > max_chars:
            line = line[: max_chars - used] + "…"
            lines.append(line)
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if lines else "(none)"


async def _rewrite_query(
    raw_query: str,
    history: list[dict],
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, dict]:
    """Return ``(rewritten_query, debug_meta)`` — falls back to ``raw_query`` on any failure.

    Skip-conditions (return ``raw_query`` immediately, with the reason
    recorded in ``meta["skipped"]``):

    * empty history (nothing to disambiguate),
    * QUERY_REWRITE_ENABLED is false (operator kill switch),
    * MISTRAL_API_KEY missing (deploy not finished).

    Failure modes (also fall through to ``raw_query``):

    * non-200 from Mistral,
    * timeout (default 1.5s — REQ-4 budget is 500ms p95 added latency),
    * empty model response,
    * any unexpected exception.

    ``debug_meta`` always contains ``rewrite_ms`` and ``was_changed: bool``.
    On skip/error it also carries ``skipped`` (string reason).
    """
    meta: dict = {"was_changed": False, "rewrite_ms": 0}
    if not raw_query or not raw_query.strip():
        meta["skipped"] = "empty_query"
        return raw_query, meta
    if not QUERY_REWRITE_ENABLED:
        meta["skipped"] = "disabled"
        return raw_query, meta
    if not history:
        meta["skipped"] = "no_history"
        return raw_query, meta
    if not MISTRAL_API_KEY:
        meta["skipped"] = "no_api_key"
        return raw_query, meta

    history_str = _format_history_for_rewrite(history)
    prompt = _QUERY_REWRITE_PROMPT.format(history=history_str, raw_query=raw_query)
    payload = {
        "model": QUERY_REWRITE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }

    client_kwargs: dict = {"timeout": QUERY_REWRITE_TIMEOUT}
    if _transport is not None:
        client_kwargs["transport"] = _transport

    t_start = time.monotonic()
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.post(MISTRAL_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        rewritten_raw = data["choices"][0]["message"]["content"]
    except Exception as exc:
        meta["rewrite_ms"] = int((time.monotonic() - t_start) * 1000)
        meta["skipped"] = "exception"
        meta["error"] = str(exc)[:120]
        logger.warning(
            "query_rewrite_failed error=%s rewrite_ms=%d",
            meta["error"],
            meta["rewrite_ms"],
        )
        return raw_query, meta

    meta["rewrite_ms"] = int((time.monotonic() - t_start) * 1000)
    rewritten = (rewritten_raw or "").strip().strip('"').strip("'")
    if not rewritten:
        meta["skipped"] = "empty_response"
        return raw_query, meta
    rewritten = rewritten[:500]
    meta["was_changed"] = rewritten.lower() != raw_query.strip().lower()
    return rewritten, meta


# ---------------------------------------------------------------------------
# SPEC-RAG-TAXONOMY-001: Combined rewrite + classify prompt
# Single LLM call returns BOTH the rewritten query AND taxonomy node IDs.
# Keeps added token cost < 100 tokens (REQ-6) vs the standalone rewrite prompt.
# ---------------------------------------------------------------------------

_QUERY_REWRITE_AND_CLASSIFY_PROMPT = (
    "You are a query rewriter and topic classifier for a RAG search system.\n\n"
    "Tasks (combined, single JSON response):\n"
    "1. Rewrite the user's current question into a self-contained search query "
    "— resolve pronouns and references using the conversation history. "
    "If already clear, return it unchanged.\n"
    "2. SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-5 — Brand-bridging: if the "
    "question mentions a third-party brand or product name (e.g. Salesforce, "
    "HubSpot, Pipedrive, Zoom, Microsoft Teams, Outlook), ALSO include "
    "2–4 broader category or related-brand terms in the rewritten query so "
    "the search can find category-specific or partner-brand pages even when "
    "the original brand string is absent from the source content. Stay "
    "within the 200-char limit; same language as the user.\n"
    "Examples:\n"
    "- 'Hoe koppel ik Voys aan Salesforce?' → 'Voys Salesforce CRM-koppeling Bubble RedCactus'\n"
    "- 'Ondersteunen jullie Zoom?' → 'Voys Zoom vergader-integratie telefoonkoppeling'\n"
    "- 'Werkt Outlook met Voys?' → 'Voys Outlook e-mailkoppeling agenda-integratie'\n"
    "If NO third-party brand is mentioned, leave the rewrite unchanged "
    "beyond the standard pronoun resolution.\n"
    "3. From the taxonomy below, select ALL node IDs whose topic is genuinely "
    "relevant to the rewritten query. An empty list means no narrowing.\n\n"
    "Conversation history (oldest → newest):\n{history}\n\n"
    "User's current question: {raw_query}\n\n"
    "Available taxonomy nodes:\n{taxonomy}\n\n"
    "Reply with ONLY a JSON object, no markdown, no explanation:\n"
    '{{"rewritten_query": "<string, max 200 chars, same language as user>", '
    '"taxonomy_node_ids": [<int>, ...]}}'
)


def _format_taxonomy_for_prompt(
    trees: dict[str, list[dict]] | list[dict],
    max_nodes_per_kb: int = 30,
) -> str:
    """Format taxonomy nodes for the classifier prompt.

    Accepts either:
      * the multi-KB shape ``{kb_slug: [node, ...]}``; renders KB-context
        labels so the LLM can disambiguate name-collisions across KBs
        ("Beleid" in voys-support vs voys-billing point at different
        IDs even though they share a name).
      * the legacy single-KB shape ``[node, ...]``; renders flat lines.

    Both branches cap node count per KB to keep prompt size bounded.
    """
    # Legacy single-KB shape.
    if isinstance(trees, list):
        if not trees:
            return "(none)"
        lines = [
            f"- id={node['id']}: {node['name']}" for node in trees[:max_nodes_per_kb]
        ]
        if len(trees) > max_nodes_per_kb:
            lines.append(f"... ({len(trees) - max_nodes_per_kb} more nodes omitted)")
        return "\n".join(lines)

    # Multi-KB shape.
    if not trees:
        return "(none)"
    lines: list[str] = []
    for kb_slug in sorted(trees.keys()):
        nodes = trees[kb_slug]
        if not nodes:
            continue
        lines.append(f"[{kb_slug}]")
        for node in nodes[:max_nodes_per_kb]:
            lines.append(f"  - id={node['id']}: {node['name']}")
        if len(nodes) > max_nodes_per_kb:
            lines.append(f"  ... ({len(nodes) - max_nodes_per_kb} more nodes omitted)")
    return "\n".join(lines) if lines else "(none)"


def _flatten_trees(trees: dict[str, list[dict]] | list[dict]) -> list[dict]:
    """Return one flat node list across all KBs (for valid_ids lookup)."""
    if isinstance(trees, list):
        return list(trees)
    flat: list[dict] = []
    for nodes in trees.values():
        flat.extend(nodes)
    return flat


# SPEC-RAG-TAXONOMY-001 (multi-KB + Redis cache).
#
# Cache key: tax_trees:{org_id}:{sorted_kb_slugs_csv}. TTL 300s — short
# enough that taxonomy changes propagate within minutes without manual
# bust, long enough that high-traffic chats hit Redis instead of the
# DB through retrieval-api on every turn.
_TAXONOMY_TREES_TTL_S = 300
_TAXONOMY_COVERAGE_TTL_S = 300
_MAX_KBS_FOR_TAXONOMY = 5


def _taxonomy_cache_keys(org_id: str, kb_slugs: list[str]) -> tuple[str, str]:
    """Stable cache keys for trees + coverage, sorted for determinism."""
    sig = ",".join(sorted(set(kb_slugs)))
    return (f"tax_trees:{org_id}:{sig}", f"tax_coverage:{org_id}:{sig}")


async def _fetch_taxonomy_trees(
    org_id: str,
    kb_slugs: list[str],
    cache,
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, list[dict]]:
    """Fetch trees for all KBs in scope. Returns ``{kb_slug: [node, ...]}``.

    Multi-KB by design: a single retrieval-api roundtrip handles N slugs.
    Redis-cached via the LiteLLM DualCache passed by the proxy — a hit
    returns the same dict shape as the live fetch. Fail-open on any
    error: returns {} so the hook proceeds without taxonomy narrowing.

    Caps at ``_MAX_KBS_FOR_TAXONOMY`` slugs — beyond that the prompt
    grows past the single-call budget and the filter is skipped.
    """
    if not TAXONOMY_ENABLED or not _RETRIEVAL_BASE_URL or not kb_slugs:
        return {}
    if len(kb_slugs) > _MAX_KBS_FOR_TAXONOMY:
        logger.info(
            "KlaiKnowledgeHook: taxonomy_skipped reason=too_many_kbs count=%d",
            len(kb_slugs),
        )
        return {}

    cache_key, _ = _taxonomy_cache_keys(org_id, kb_slugs)
    if cache is not None:
        try:
            cached = await cache.async_get_cache(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict):
            return cached

    url = f"{_RETRIEVAL_BASE_URL}/internal/v1/taxonomy/trees"
    legacy_headers = _retrieve_legacy_headers()
    client_kwargs: dict = {"timeout": TAXONOMY_FETCH_TIMEOUT}
    if _transport is not None:
        client_kwargs["transport"] = _transport

    # httpx serialises list[str] params as repeated query keys when given
    # a list of (key, value) tuples — that's the shape FastAPI's
    # ``Query(...)`` decoder expects for kb_slugs=a&kb_slugs=b.
    params = [("org_id", org_id)] + [("kb_slugs", s) for s in kb_slugs]
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(url, params=params, headers=legacy_headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(
            "KlaiKnowledgeHook: taxonomy_trees_fetch_failed org=%s kbs=%s error=%s",
            org_id,
            kb_slugs,
            str(exc)[:120],
        )
        return {}

    if not isinstance(data, dict):
        return {}

    if cache is not None:
        try:
            await cache.async_set_cache(cache_key, data, ttl=_TAXONOMY_TREES_TTL_S)
        except Exception:
            pass
    return data


async def _fetch_taxonomy_coverage(
    org_id: str,
    kb_slugs: list[str],
    cache,
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, float]:
    """Fetch per-KB coverage ratios. Returns ``{kb_slug: 0.0|1.0}``.

    Redis-cached. Missing KBs default to 0.0. Fail-open on any error.
    """
    if not TAXONOMY_ENABLED or not _RETRIEVAL_BASE_URL or not kb_slugs:
        return {slug: 0.0 for slug in kb_slugs}

    _, cache_key = _taxonomy_cache_keys(org_id, kb_slugs)
    if cache is not None:
        try:
            cached = await cache.async_get_cache(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict):
            return {slug: float(cached.get(slug, 0.0)) for slug in kb_slugs}

    url = f"{_RETRIEVAL_BASE_URL}/internal/v1/taxonomy/coverage"
    legacy_headers = _retrieve_legacy_headers()
    client_kwargs: dict = {"timeout": TAXONOMY_FETCH_TIMEOUT}
    if _transport is not None:
        client_kwargs["transport"] = _transport
    params = [("org_id", org_id)] + [("kb_slugs", s) for s in kb_slugs]
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(url, params=params, headers=legacy_headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(
            "KlaiKnowledgeHook: taxonomy_coverage_fetch_failed org=%s kbs=%s error=%s",
            org_id,
            kb_slugs,
            str(exc)[:120],
        )
        return {slug: 0.0 for slug in kb_slugs}

    coverage = {slug: float(data.get(slug, 0.0)) for slug in kb_slugs}
    if cache is not None:
        try:
            await cache.async_set_cache(
                cache_key, coverage, ttl=_TAXONOMY_COVERAGE_TTL_S
            )
        except Exception:
            pass
    return coverage


async def _rewrite_and_classify(
    raw_query: str,
    history: list[dict],
    taxonomy_trees: dict[str, list[dict]] | list[dict],
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, list[int], dict]:
    """Return ``(rewritten_query, classified_node_ids, debug_meta)`` in one LLM call.

    Accepts either the multi-KB shape ``{kb_slug: [node, ...]}`` or the
    legacy single-KB ``[node, ...]`` for backward compat. Internally the
    classifier sees one merged prompt with KB-context labels (multi-KB)
    or a flat list (single-KB). Returned IDs are always the unfiltered
    flat unique-id space — retrieval-api's ANY-of filter does the rest.

    Replaces the standalone ``_rewrite_query`` call when at least one
    KB has taxonomy nodes. With empty trees it falls back to plain
    rewrite (no classification).

    Anti-hallucination guard (REQ-4): only returns node IDs that exist
    in the union of all provided trees.

    Fail-open on any error: falls back to (raw_query, [], meta_with_skip_reason).
    """
    import json as _json

    meta: dict = {"was_changed": False, "rewrite_ms": 0}

    # Early exits (same skip conditions as _rewrite_query)
    if not raw_query or not raw_query.strip():
        meta["skipped"] = "empty_query"
        return raw_query, [], meta
    if not QUERY_REWRITE_ENABLED:
        meta["skipped"] = "disabled"
        return raw_query, [], meta
    if not MISTRAL_API_KEY:
        meta["skipped"] = "no_api_key"
        return raw_query, [], meta

    flat_tree = _flatten_trees(taxonomy_trees)

    # No history AND no taxonomy → nothing to do
    if not history and not flat_tree:
        meta["skipped"] = "no_history_no_tree"
        return raw_query, [], meta

    # Fall back to plain text prompt when no taxonomy is available
    if not flat_tree:
        rewritten, rewrite_meta = await _rewrite_query(
            raw_query, history, _transport=_transport
        )
        return rewritten, [], rewrite_meta

    # Build combined prompt
    history_str = _format_history_for_rewrite(history) if history else "(none)"
    taxonomy_str = _format_taxonomy_for_prompt(taxonomy_trees)
    prompt = _QUERY_REWRITE_AND_CLASSIFY_PROMPT.format(
        history=history_str,
        raw_query=raw_query,
        taxonomy=taxonomy_str,
    )
    payload = {
        "model": QUERY_REWRITE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 300,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }
    client_kwargs: dict = {"timeout": QUERY_REWRITE_TIMEOUT}
    if _transport is not None:
        client_kwargs["transport"] = _transport

    valid_ids: set[int] = {int(n["id"]) for n in flat_tree}
    t_start = time.monotonic()
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.post(MISTRAL_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            raw_content = resp.json()["choices"][0]["message"]["content"]
        parsed = _json.loads(raw_content or "{}")
    except Exception as exc:
        meta["rewrite_ms"] = int((time.monotonic() - t_start) * 1000)
        meta["skipped"] = "exception"
        meta["error"] = str(exc)[:120]
        logger.warning(
            "query_rewrite_and_classify_failed error=%s rewrite_ms=%d",
            meta["error"],
            meta["rewrite_ms"],
        )
        return raw_query, [], meta

    meta["rewrite_ms"] = int((time.monotonic() - t_start) * 1000)

    # Extract rewritten query
    rewritten = (parsed.get("rewritten_query") or "").strip().strip('"').strip("'")
    if not rewritten:
        meta["skipped"] = "empty_rewritten_query"
        return raw_query, [], meta
    rewritten = rewritten[:500]
    meta["was_changed"] = rewritten.lower() != raw_query.strip().lower()

    # Extract and validate taxonomy node IDs (anti-hallucination guard REQ-4)
    raw_ids = parsed.get("taxonomy_node_ids") or []
    classified: list[int] = []
    for item in raw_ids:
        try:
            node_id = int(item)
        except (TypeError, ValueError):
            continue
        if node_id in valid_ids:
            classified.append(node_id)

    meta["classified_node_ids"] = classified
    return rewritten, classified, meta


async def _get_kb_feature(user_id: str, org_id: str, cache) -> dict:
    """Return the user's KB feature state including entitlement and scope preference.

    Two-level cache strategy:
    - Version pointer (kb_ver:...) — 30s TTL. Expires when kb_pref_version increments,
      forcing a fresh portal fetch within 30s of a preference change.
    - Feature data (kb_feature:...:version) — 300s TTL.

    Fail-closed for entitlement: portal errors return enabled=False.
    Fail-open for retrieval preference: portal errors leave kb_retrieval_enabled=True
    so existing retrieval behavior is preserved (REQ-N1).

    Backward compatible: handles old {"enabled": bool} portal responses gracefully.
    """
    if not PORTAL_INTERNAL_SECRET:
        logger.warning(
            "KlaiKnowledgeHook: PORTAL_INTERNAL_SECRET not set — fail-closed"
        )
        return {
            "enabled": False,
            "kb_retrieval_enabled": True,
            "kb_personal_enabled": True,
            "kb_slugs_filter": None,
            "kb_narrow": False,
            "version": 0,
            "zitadel_user_id": None,
            # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-4: fail-open to 'shadow', never 'off'.
            "telemetry_level": "shadow",
        }

    # Step 1: check version pointer (short-lived — invalidated by preference changes)
    version_key = f"kb_ver:{org_id}:{user_id}"
    cached_version = await cache.async_get_cache(version_key)

    if cached_version is not None:
        feature_key = f"kb_feature:{org_id}:{user_id}:{cached_version}"
        cached = await cache.async_get_cache(feature_key)
        if cached is not None:
            return cached

    # Cache miss — fetch fresh from portal
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(
                f"{PORTAL_API_URL}/internal/v1/users/{user_id}/feature/knowledge",
                params={"org_id": org_id},
                headers={"Authorization": f"Bearer {PORTAL_INTERNAL_SECRET}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(
            "KlaiKnowledgeHook: portal feature fetch failed (%s) — fail-closed", exc
        )
        return {
            "enabled": False,
            "kb_retrieval_enabled": True,
            "kb_personal_enabled": True,
            "kb_slugs_filter": None,
            "kb_narrow": False,
            "version": 0,
            "zitadel_user_id": None,
            # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-4: fail-open to 'shadow', never 'off'.
            # Silent telemetry is the wrong default during outages.
            "telemetry_level": "shadow",
        }

    version = data.get("kb_pref_version", 0)
    result = {
        "enabled": data.get("enabled", False),
        "kb_retrieval_enabled": data.get("kb_retrieval_enabled", True),
        "kb_personal_enabled": data.get("kb_personal_enabled", True),
        "kb_slugs_filter": data.get("kb_slugs_filter"),
        "kb_narrow": data.get("kb_narrow", False),
        "version": version,
        # SPEC-SEC-IDENTITY-ASSERT-001 follow-up: portal-api maps the LibreChat
        # ObjectId we pass in the URL to the portal_users row and exposes the
        # canonical Zitadel sub here. retrieval-api's identity-verify path,
        # personal-KB qdrant filter, and the verify-cache key all match on
        # zitadel_user_id — using the LibreChat ObjectId would 403 every call.
        "zitadel_user_id": data.get("zitadel_user_id"),
        # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-2: per-tenant telemetry mode.
        # Older portal-api builds without the field land in the default
        # 'shadow' (REQ-4 fail-open) so a mid-deploy state is privacy-safe.
        "telemetry_level": data.get("telemetry_level", "shadow"),
    }

    # Store version pointer (30s) and feature data (300s) separately
    await cache.async_set_cache(version_key, str(version), ttl=30)
    await cache.async_set_cache(
        f"kb_feature:{org_id}:{user_id}:{version}", result, ttl=300
    )
    return result


# @MX:NOTE: classify_gap, fire_gap_event, fire_retrieval_log moved to
# klai-libs/retrieval-telemetry/ (SPEC-MCP-RETRIEVAL-001 Phase 1). Imports
# at the top of this module rebind them to the underscore-prefixed names
# every call site below already uses, so the move is invisible to callers.
# The threshold / URL env vars (KLAI_GAP_SOFT_THRESHOLD,
# KLAI_GAP_DENSE_THRESHOLD, EMBEDDING_MODEL_VERSION,
# PORTAL_RETRIEVAL_LOG_URL) are read inside the lib's _default_config()
# helper at call time, so the env-rotation behaviour stays identical.


# ---------------------------------------------------------------------------
# SPEC-CHAT-TEMPLATES-001: prompt-template fetch.
#
# Fetched from portal-api `/internal/templates/effective` (server-side
# resolution: org → user → active_template_ids → filtered + ordered).
# Cached 30s per (org_id, user_id) via LiteLLM's shared cache, same
# pattern as _get_kb_feature.
#
# Fail-open: any timeout / 5xx / 401 / bad secret → empty list + warning.
# Chat MUST never break because the templates fetch failed.
#
# @MX:WARN: This helper is fail-open by design. A silent `templates_degraded`
# log line is the ONLY signal when portal-api can't be reached. Observability
# must alert on a sustained non-zero rate of these warnings.
# @MX:REASON: templates are a convenience feature; blocking a chat call to
# preserve styling would be a worse user experience than losing the styling.
# ---------------------------------------------------------------------------


async def _get_templates(org_id: str, user_id: str, cache) -> list[dict]:
    """Return active prompt-template instructions for (org, user).

    Shape: ``[{"source": "template", "name": str, "text": str}, ...]``
    Empty list when the user has no active templates, the portal-api is
    unreachable, or PORTAL_INTERNAL_SECRET is unset.

    Cached 30 s per (org_id, user_id) — same TTL as KB feature flag so
    toggling active_template_ids takes effect in at most 30 s when Redis
    invalidation misses.
    """
    if not PORTAL_INTERNAL_SECRET:
        # Fail-closed on missing secret: without auth we can't call portal-api.
        return []

    cache_key = f"templates:{org_id}:{user_id}"
    cached = await cache.async_get_cache(cache_key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=TEMPLATES_TIMEOUT) as client:
            resp = await client.get(
                PORTAL_TEMPLATES_URL,
                params={"zitadel_org_id": org_id, "librechat_user_id": user_id},
                headers={"Authorization": f"Bearer {PORTAL_INTERNAL_SECRET}"},
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as exc:
        # 401 → config error (bad or missing internal secret); log distinctly.
        if exc.response is not None and exc.response.status_code == 401:
            logger.error(
                "KlaiKnowledgeHook: templates_config_error org=%s user=%s (bad internal secret)",
                org_id,
                user_id,
            )
        else:
            logger.warning(
                "KlaiKnowledgeHook: templates_degraded org=%s user=%s reason=%s",
                org_id,
                user_id,
                exc,
            )
        instructions: list[dict] = []
    except Exception as exc:
        logger.warning(
            "KlaiKnowledgeHook: templates_degraded org=%s user=%s reason=%s",
            org_id,
            user_id,
            exc,
        )
        instructions = []
    else:
        instructions = payload.get("instructions") or []

    # Cache even the empty result: a user with no active templates shouldn't
    # retry the portal round-trip on every message.
    await cache.async_set_cache(cache_key, instructions, ttl=30)
    return instructions


def _prepend_system_prefix(messages: list[dict], prefix: str) -> None:
    """Prepend `prefix` to the system message (or insert one if none exists).

    Mutates `messages` in-place. No-op when `prefix` is empty.

    Separated from the hook body so templates-only and templates+KB paths
    share the same insertion logic. Unit-testable without a running hook.
    """
    if not prefix:
        return
    sys_idx = next(
        (i for i, m in enumerate(messages) if m.get("role") == "system"), None
    )
    if sys_idx is not None:
        existing = messages[sys_idx].get("content", "")
        messages[sys_idx] = {
            "role": "system",
            "content": f"{prefix}\n\n{existing}" if existing else prefix,
        }
    else:
        messages.insert(0, {"role": "system", "content": prefix})


def _build_template_instructions_block(instructions: list[dict]) -> str:
    """Render template list into a single system-prompt prefix block.

    Empty list returns "" — caller MUST check before prepending.
    Only the template's `name` and `text` appear in the block. Raw
    template text never goes to logs (REQ-TEMPLATES-HOOK-N2).
    """
    if not instructions:
        return ""
    # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): English-prefixed wrapper.
    # The model receives English instructions but answers in the language
    # detected by GROUNDED_CHAT_SYSTEM_PROMPT (prepended above this block at
    # the call site). Template `name` and `text` themselves are tenant-defined
    # — they may already be in any language; we don't translate them.
    parts: list[str] = [
        "[Klai Templates — apply the following instructions to your answer]"
    ]
    for inst in instructions:
        name = inst.get("name") or "template"
        text = (inst.get("text") or "").strip()
        if not text:
            continue
        parts.append(f"[{name}]\n{text}")
    parts.append("[End templates]")
    return "\n\n".join(parts)


def _compose_libre_chat_prefix(*blocks: str) -> str:
    """Compose the system-prompt prefix for path A (LibreChat → LiteLLM).

    SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10). Leads with
    ``GROUNDED_CHAT_SYSTEM_PROMPT`` so language detection / 3-guard switch
    semantics apply, AND so the model defaults to KB-grounded behaviour on
    every branch where a KB scope is in play — even when retrieval returned
    zero chunks or failed loud. Empty blocks are filtered out.

    Used by every code path downstream of ``if not librechat_user_id:
    return data`` EXCEPT the explicit "user opted out of every KB scope"
    branch, which calls :func:`_compose_general_chat_prefix` instead.
    Paths B (partner_chat) and C (retrieval-api /chat) construct their
    own prompts via the canonical ``klai_chat_prompts`` library — they do
    NOT route through this helper.
    """
    return "\n\n".join(b for b in (GROUNDED_CHAT_SYSTEM_PROMPT, *blocks) if b)


def _normalise_guard_url(url: object) -> str:
    if not isinstance(url, str):
        return ""
    value = url.strip().strip("<>")
    if not value or value.lower() in _SENTINEL_URLS:
        return ""
    if value.startswith("/"):
        return value

    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def _chunk_source_url(chunk: dict[str, Any]) -> str:
    candidates: list[object] = [
        chunk.get("source_url"),
        chunk.get("sourceUrl"),
        chunk.get("canonical_url"),
        chunk.get("page_url"),
        chunk.get("url"),
    ]
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("source_url"),
                metadata.get("sourceUrl"),
                metadata.get("canonical_url"),
                metadata.get("page_url"),
                metadata.get("url"),
            ]
        )
    source = chunk.get("source")
    if isinstance(source, dict):
        candidates.extend(
            [
                source.get("source_url"),
                source.get("url"),
                source.get("href"),
            ]
        )

    for candidate in candidates:
        normalised = _normalise_guard_url(candidate)
        if normalised and not normalised.startswith("/"):
            return normalised
    return ""


def _chunk_title(chunk: dict[str, Any]) -> str:
    metadata = chunk.get("metadata")
    title = chunk.get("title")
    if not title and isinstance(metadata, dict):
        title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "Source"


def _absolute_image_url(url: object) -> str:
    normalised = _normalise_guard_url(url)
    if not normalised:
        return ""
    return (
        f"{KB_IMAGES_BASE_URL}{normalised}"
        if normalised.startswith("/")
        else normalised
    )


def _get_choice_message(choice: object, key: str) -> object:
    if isinstance(choice, dict):
        return choice.get(key)
    return getattr(choice, key, None)


def _get_message_content(message: object) -> object:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def _get_choice_finish_reason(choice: object) -> object:
    if isinstance(choice, dict):
        return choice.get("finish_reason")
    return getattr(choice, "finish_reason", None)


def _set_message_content(message: object, content: object) -> None:
    if isinstance(message, dict):
        message["content"] = content
    else:
        setattr(message, "content", content)


def _no_citable_sources_message(user_query: object) -> str:
    query = user_query if isinstance(user_query, str) else ""
    dutch_markers = {"wat", "waar", "welke", "hoe", "waarom", "gegevens", "bronnen", "klopt"}
    query_tokens = {token.lower() for token in re.findall(r"[a-zA-ZÀ-ÿ]+", query)}
    if query_tokens & dutch_markers:
        return "Ik kan dit niet betrouwbaar beantwoorden op basis van de beschikbare kennisbronnen."
    return "I cannot answer this reliably from the available knowledge sources."


def _get_response_choices(response: object) -> object:
    if isinstance(response, dict):
        return response.get("choices") or []
    return getattr(response, "choices", []) or []


def _normalise_evidence_pack_sources(evidence_pack: object) -> list[dict[str, Any]]:
    if not isinstance(evidence_pack, dict):
        return []
    sources = evidence_pack.get("sources")
    if not isinstance(sources, list):
        return []
    rendered: list[dict[str, Any]] = []
    for index, source in enumerate(sources, 1):
        if not isinstance(source, dict):
            continue
        url = _normalise_guard_url(source.get("source_url") or source.get("url"))
        if not url:
            continue
        rendered.append(
            {
                "label": str(index),
                "title": source.get("title") or "Source",
                "url": url,
                "source_id": source.get("source_id"),
                "evidence_ids": source.get("evidence_ids") or [],
                "artifact_id": source.get("artifact_id"),
                "source_label": source.get("source_label"),
                "relevance_score": source.get("relevance_score"),
            }
        )
    return rendered


def _evidence_pack_items_as_chunks(evidence_pack: object) -> list[dict[str, Any]]:
    if not isinstance(evidence_pack, dict):
        return []
    items = evidence_pack.get("items")
    if not isinstance(items, list):
        return []
    chunks: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        chunks.append(
            {
                "chunk_id": item.get("chunk_id"),
                "artifact_id": item.get("artifact_id"),
                "content_type": item.get("content_type"),
                "text": item.get("text"),
                "title": item.get("title"),
                "heading_path": item.get("heading_path"),
                "source_url": item.get("source_url"),
                "source_label": item.get("source_label"),
                "score": item.get("score"),
                "reranker_score": item.get("reranker_score"),
                "final_score": item.get("final_score"),
                "scope": item.get("scope"),
                "image_urls": item.get("image_urls"),
                "is_parent_text": item.get("is_parent_text"),
            }
        )
    return chunks


def _citation_render_inputs(kb_meta: dict[str, Any]) -> tuple[set[str], list[dict], list[dict[str, Any]]]:
    allowed_image_urls = {
        url
        for url in (
            _normalise_guard_url(url) for url in kb_meta.get("allowed_image_urls") or []
        )
        if url
    }
    citation_chunks = [
        chunk for chunk in (kb_meta.get("citation_chunks") or []) if isinstance(chunk, dict)
    ]
    trusted_sources = [
        source
        for source in (kb_meta.get("trusted_sources") or [])
        if isinstance(source, dict) and _normalise_guard_url(source.get("url"))
    ]
    return allowed_image_urls, citation_chunks, trusted_sources


@dataclass
class _KbCitationRenderStats:
    mutated_messages: int = 0
    rendered_messages: int = 0
    rendered_sources: int = 0
    no_citable_sources: bool = False

    def merge(self, other: "_KbCitationRenderStats") -> None:
        self.mutated_messages += other.mutated_messages
        self.rendered_messages += other.rendered_messages
        self.rendered_sources = max(self.rendered_sources, other.rendered_sources)
        self.no_citable_sources = self.no_citable_sources or other.no_citable_sources


def _render_kb_citation_content(
    text: str,
    registry: CitationRegistry,
    *,
    allowed_image_urls: set[str],
    user_query: object,
    trusted_sources: list[dict[str, Any]] | None = None,
) -> tuple[str, int, bool]:
    if trusted_sources is not None:
        if not trusted_sources:
            return _no_citable_sources_message(user_query), 0, True
        cleaned = strip_model_citation_artifacts(text, allowed_image_urls=allowed_image_urls)
        sources_markdown = format_sources_markdown(trusted_sources)
        if not cleaned or not sources_markdown:
            return _no_citable_sources_message(user_query), 0, True
        return f"{cleaned}\n\n{sources_markdown}", len(trusted_sources), False

    if not getattr(registry, "has_sources", False):
        return _no_citable_sources_message(user_query), 0, True
    rendered = render_markdown_answer(
        text,
        registry,
        allowed_image_urls=allowed_image_urls,
    )
    if not rendered.sources:
        return _no_citable_sources_message(user_query), 0, True
    return rendered.content, len(rendered.sources), False


def _log_kb_citation_render(
    kb_meta: dict[str, Any],
    stats: _KbCitationRenderStats,
    *,
    stream: bool,
) -> None:
    if not stats.rendered_messages:
        return

    event = (
        "kb_citations_no_citable_sources"
        if stats.no_citable_sources
        else "kb_citations_rendered_markdown"
    )
    logger.warning(
        "%s org_id=%s user_id=%s render_mode=%s stream=%s rendered_messages=%d rendered_sources=%d chunks_injected=%s",
        event,
        kb_meta.get("org_id"),
        kb_meta.get("user_id"),
        kb_meta.get("render_mode"),
        stream,
        stats.rendered_messages,
        stats.rendered_sources,
        kb_meta.get("chunks_injected"),
    )


def _flush_citation_stream_buffer(
    response: object,
    kb_meta: dict[str, Any],
    citation_chunks: list[dict],
    allowed_image_urls: set[str],
) -> _KbCitationRenderStats:
    stats = _KbCitationRenderStats()
    stream_parts = kb_meta.get("_citation_stream_parts") or []
    full_text = "".join(part for part in stream_parts if isinstance(part, str))
    if not full_text:
        return stats

    trusted_sources = kb_meta.get("trusted_sources")
    force_no_citable = bool(kb_meta.get("no_citable_sources"))
    registry = build_citation_registry(citation_chunks)
    rendered_content, rendered_sources, no_citable_sources = _render_kb_citation_content(
        full_text,
        registry,
        allowed_image_urls=allowed_image_urls,
        user_query=kb_meta.get("user_query"),
        trusted_sources=trusted_sources
        if isinstance(trusted_sources, list) and (trusted_sources or force_no_citable)
        else None,
    )

    for choice in _get_response_choices(response):
        delta = _get_choice_message(choice, "delta")
        if delta is None:
            continue
        _set_message_content(delta, rendered_content)
        stream_parts.clear()
        stats.mutated_messages += 1
        stats.rendered_messages += 1
        stats.rendered_sources = rendered_sources
        stats.no_citable_sources = no_citable_sources
        return stats
    return stats


def _compose_non_streaming_kb_response(
    response: object,
    kb_meta: dict[str, Any],
) -> _KbCitationRenderStats:
    """Replace non-streaming message content with deterministic citations."""
    stats = _KbCitationRenderStats()
    allowed_image_urls, citation_chunks, trusted_sources = _citation_render_inputs(kb_meta)
    force_no_citable = bool(kb_meta.get("no_citable_sources"))
    if not citation_chunks and not trusted_sources and not force_no_citable:
        return stats
    registry = build_citation_registry(citation_chunks)

    for choice in _get_response_choices(response):
        message = _get_choice_message(choice, "message")
        if message is None:
            continue
        content = _get_message_content(message)
        if isinstance(content, str):
            rendered_content, rendered_sources, no_citable_sources = _render_kb_citation_content(
                content,
                registry,
                allowed_image_urls=allowed_image_urls,
                user_query=kb_meta.get("user_query"),
                trusted_sources=trusted_sources if trusted_sources or force_no_citable else None,
            )
            if rendered_content != content:
                _set_message_content(message, rendered_content)
                stats.mutated_messages += 1
                stats.rendered_messages += 1
                stats.rendered_sources = max(stats.rendered_sources, rendered_sources)
                stats.no_citable_sources = stats.no_citable_sources or no_citable_sources
    return stats


def _compose_streaming_kb_response(
    response: object,
    kb_meta: dict[str, Any],
    *,
    flush_stream: bool = False,
) -> _KbCitationRenderStats:
    """Buffer streaming deltas and flush one deterministic cited delta."""
    stats = _KbCitationRenderStats()
    allowed_image_urls, citation_chunks, trusted_sources = _citation_render_inputs(kb_meta)
    force_no_citable = bool(kb_meta.get("no_citable_sources"))
    if not citation_chunks and not trusted_sources and not force_no_citable:
        return stats

    for choice in _get_response_choices(response):
        delta = _get_choice_message(choice, "delta")
        if delta is None:
            continue
        content = _get_message_content(delta)
        stream_parts = kb_meta.setdefault("_citation_stream_parts", [])
        if isinstance(content, str) and content:
            stream_parts.append(content)
            _set_message_content(delta, "")
            stats.mutated_messages += 1
        if flush_stream or _get_choice_finish_reason(choice):
            stats.merge(
                _flush_citation_stream_buffer(
                    response,
                    kb_meta,
                    citation_chunks,
                    allowed_image_urls,
                )
            )
    return stats


def _compose_general_chat_prefix(*blocks: str) -> str:
    """Compose the system-prompt prefix for path A when the user has
    explicitly opted out of every KB scope (``kb_personal_enabled=False``
    AND ``kb_slugs_filter=[]``).

    This is the "Algemene AI" branch the chat-config dropdown surfaces.
    Same language-detection contract as
    :func:`_compose_libre_chat_prefix`, but the KB-grounding rules are
    swapped for general-assistant rules so the model:

    * does NOT emit [n] citations,
    * does NOT say 'Dat staat niet in de kennisbank' for every question,
    * answers from general knowledge without pretending to have sources.

    Templates (active per-user/per-org template instructions) still
    apply on this branch — they are user-controlled prompt fragments
    independent of KB scope.

    Reachable only from the no-KB branch in
    :class:`KlaiKnowledgeHook.async_pre_call_hook`. All other branches
    (no-entitlement, kb_retrieval disabled, retrieval failed, zero
    chunks) keep using :func:`_compose_libre_chat_prefix` so the model
    still acts as a KB assistant when a KB scope was intended but data
    was unavailable.
    """
    return "\n\n".join(b for b in (GENERAL_CHAT_SYSTEM_PROMPT, *blocks) if b)


def _compose_meta_chat_prefix(*blocks: str) -> str:
    """Compose the system-prompt prefix for path A when the user is asking
    a META question about Klai itself ("what is Klai", "what can I do
    here", "how does this chat work") rather than a content question.

    Triggered by :func:`_is_meta_query` matching the latest user message.
    Same language-detection contract as the other two prefixes, but the
    KB-grounding rules are swapped for capability-explanation rules so
    the model:

    * does NOT emit [n] citations (no chunks in scope),
    * does NOT pull or quote any KB content (the user didn't ask for any),
    * describes Klai at the level of "what kind of thing it is", not a
      fabricated feature list.

    Templates still apply on this branch — admins may want to override
    or extend the canned capability description per org. Reachable only
    from the meta-query branch in
    :class:`KlaiKnowledgeHook.async_pre_call_hook` (path A only). Paths
    B and C are server-to-server and never see meta-questions.

    Background: 2026-05-12 Voys "Meldingen" incident. Without this
    early-return path, a vague meta-question ("wat kan ik hier?") fell
    through to retrieval, matched a tangential KB chunk on common
    words, and the model defended that match as the answer.
    """
    return "\n\n".join(b for b in (META_CHAT_SYSTEM_PROMPT, *blocks) if b)


class KlaiKnowledgeHook(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if call_type not in ("completion", "acompletion"):
            return data

        messages = data.get("messages", [])
        query = _last_user_message(messages)
        if not query or _is_trivial(query):
            return data

        # org_id lives in LiteLLM team key metadata
        metadata = getattr(user_api_key_dict, "metadata", {}) or {}
        org_id = metadata.get("org_id")
        if not org_id:
            # Master key usage — no org scope available, skip silently
            return data

        # librechat_user_id = LibreChat MongoDB ObjectId sent as the "user" field.
        # We only use this against portal-api endpoints that explicitly accept
        # the LibreChat ID (kb_feature, templates). Everything that asks
        # "which Zitadel user is this?" — including retrieval-api's
        # /retrieve identity-verify path and the personal-KB qdrant filter —
        # gets the resolved zitadel_user_id from the kb_feature response.
        librechat_user_id = data.get("user", "")
        if not librechat_user_id:
            return data

        if _is_title_generation_request(messages):
            logger.info(
                "title_generation_request_detected org_id=%s user_id=%s",
                org_id,
                librechat_user_id,
            )
            return data

        # SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-HOOK: fetch active templates
        # before the KB path so they apply on EVERY downstream branch —
        # including early returns when KB retrieval is skipped or fails.
        # Fail-open: empty list on any portal-api error.
        template_instructions = await _get_templates(org_id, librechat_user_id, cache)
        templates_block = _build_template_instructions_block(template_instructions)

        # Meta-question early-return (Voys "Meldingen" incident, 2026-05-12).
        # When the user asks ABOUT Klai itself ("wat kan ik hier?", "what is
        # Klai?", "how does this chat work?") rather than asking a content
        # question, skip retrieval entirely and inject META_CHAT_SYSTEM_PROMPT
        # so the model gives a plain capability-description answer without
        # fabricating features or grasping at tangential KB chunks.
        #
        # Sits AFTER _is_trivial / org_id / librechat_user_id checks so it
        # only fires inside a valid path-A request. Sits BEFORE the KB
        # feature flag because a user without KB entitlement asking "what
        # is Klai?" still deserves a real answer — not the generic libre
        # prefix that promises KB grounding.
        #
        # Templates still apply so admins can extend the canned capability
        # description per org.
        if _is_meta_query(query):
            _prepend_system_prefix(messages, _compose_meta_chat_prefix(templates_block))
            data["messages"] = messages
            logger.info(
                "meta_query_detected org_id=%s user_id=%s query=%r",
                org_id,
                librechat_user_id,
                query[:80],
            )
            return data

        # Feature gate + KB scope preference (version-based cache, 30s propagation)
        feature = await _get_kb_feature(librechat_user_id, org_id, cache)
        if not feature["enabled"]:
            # No KB entitlement. Templates still apply. Multilingual foundation
            # still applies — REQ-10: every LibreChat path is multilingual.
            _prepend_system_prefix(
                messages, _compose_libre_chat_prefix(templates_block)
            )
            data["messages"] = messages
            return data

        if not feature["kb_retrieval_enabled"]:
            # User disabled KB retrieval. Templates still apply. Multilingual
            # foundation still applies — REQ-10.
            _prepend_system_prefix(
                messages, _compose_libre_chat_prefix(templates_block)
            )
            data["messages"] = messages
            return data

        # SPEC-SEC-IDENTITY-ASSERT-001 follow-up: retrieval-api forwards
        # claimed_user_id to portal-api /internal/identity/verify which
        # matches against PortalUser.zitadel_user_id. Sending the LibreChat
        # ObjectId here lands on `reason: "no_membership"` and a 403
        # `identity_assertion_failed` — observed in prod 2026-05-05 once
        # the missing-X-Caller-Service header bug was fixed.
        #
        # From here on we use `user_id` (= Zitadel sub) for everything that
        # leaves this process: retrieval-api request body, gap-events,
        # retrieval-log, _klai_kb_meta. Personal-KB qdrant chunks are
        # stamped with the same Zitadel sub at ingest time
        # (klai-portal/backend/app/api/knowledge.py:172-204) so the
        # personal-scope filter matches.
        user_id: str | None = feature.get("zitadel_user_id")
        if not user_id:
            # portal-api couldn't resolve the LibreChat ObjectId → Zitadel sub.
            # Without it, retrieval-api's identity-verify denies. Surface to
            # the user via the existing fail-loud path instead of degrading.
            logger.error(
                "KlaiKnowledgeHook: portal returned no zitadel_user_id for "
                "librechat_user_id=%s org_id=%s — failing loud",
                librechat_user_id,
                org_id,
            )
            # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): English
            # instruction to the model; the warning the model emits is in the
            # user's detected language thanks to GROUNDED_CHAT_SYSTEM_PROMPT.
            kb_unavailable_notice = (
                "[Klai Knowledge Base — TEMPORARILY UNAVAILABLE. Answer using "
                "your general knowledge. Begin your answer with a warning to "
                "the user, written in the language you detected from their "
                "most recent substantive message: tell them you could not "
                "reach the knowledge base (technical reason: "
                "identity-resolve-failed), this answer is therefore not "
                "based on their own documentation, and they should try "
                "again later.]\n"
            )
            prefix = _compose_libre_chat_prefix(templates_block, kb_unavailable_notice)
            _prepend_system_prefix(messages, prefix)
            data["messages"] = messages
            return data

        # Determine retrieval scope, KB slug filter, and answer mode.
        #
        # `kb_slugs_filter` tri-state contract (matches portal-api +
        # ChatConfigBar dropdown):
        #   None   = "all org KBs" (default)
        #   []     = "no org KBs"  (user turned every collection off)
        #   [...]  = explicit subset
        #
        # Combined with `kb_personal_enabled`, this gives 6 reachable
        # states. The historic `if kb_slugs:` branch treated `[]` and
        # `None` identically, so a user who turned EVERY org collection
        # off (and personal too) silently still got every org chunk
        # injected — exact opposite of intent.
        kb_personal = feature.get("kb_personal_enabled", True)
        kb_slugs = feature.get("kb_slugs_filter")
        kb_narrow = feature.get("kb_narrow", False)

        # User explicitly opted out of every scope → "Algemene AI" mode.
        # Skip retrieval AND swap KB-grounding instructions for
        # general-assistant instructions so the model doesn't keep
        # answering "Dat staat niet in de kennisbank" with no KB present.
        # Templates may still apply (REQ-TEMPLATES-HOOK-U2 fail-open).
        # Multilingual foundation still applies — REQ-10 (the language
        # contract is shared between GROUNDED and GENERAL prompts).
        if not kb_personal and kb_slugs == []:
            _prepend_system_prefix(
                messages,
                _compose_general_chat_prefix(
                    _general_runtime_capabilities_block(data),
                    templates_block,
                ),
            )
            data["messages"] = messages
            return data

        # Translate (kb_personal, kb_slugs) → retrieval-api `scope` + optional
        # `kb_slugs` filter.
        if kb_personal and kb_slugs == []:
            # Personal-only: no org KBs at all.
            scope = "personal"
            kb_slugs_for_request: list[str] | None = None
        else:
            scope = "both" if kb_personal else "org"
            kb_slugs_for_request = kb_slugs if kb_slugs else None

        conversation_history = _build_conversation_history(messages)

        # SPEC-RAG-TAXONOMY-001 (multi-KB): fetch taxonomy trees + coverage for
        # every KB the request is scoped to in a single retrieval-api roundtrip,
        # Redis-cached. v1 was first-KB-only; v2 merges trees across KBs and
        # uses per-KB coverage so a mixed-coverage request still benefits from
        # the KBs that *do* have a curated taxonomy.
        #
        # Scope rules:
        #   * `kb_slugs_for_request is None` → "all org KBs". We don't know the
        #     full set client-side, so taxonomy is skipped (no_kbs_in_scope).
        #     Fail-open: retrieval-api still applies its org/scope filters.
        #   * personal-only scope ("personal") → no org KBs → skip taxonomy.
        kbs_in_scope: list[str] = (
            list(kb_slugs_for_request) if kb_slugs_for_request else []
        )
        taxonomy_trees: dict[str, list[dict]] = {}
        taxonomy_coverage_map: dict[str, float] = {}
        if kbs_in_scope and TAXONOMY_ENABLED and scope in ("org", "both"):
            taxonomy_trees, taxonomy_coverage_map = await asyncio.gather(
                _fetch_taxonomy_trees(org_id, kbs_in_scope, cache),
                _fetch_taxonomy_coverage(org_id, kbs_in_scope, cache),
            )

        # Filter to KBs that meet the coverage threshold. The classifier sees
        # ONLY trees from these KBs so it cannot return IDs from a KB without
        # curated taxonomy (REQ-1+2: low-coverage KBs run unfiltered).
        kbs_with_coverage: list[str] = [
            slug
            for slug in kbs_in_scope
            if taxonomy_coverage_map.get(slug, 0.0) >= KLAI_TAXONOMY_COVERAGE_THRESHOLD
        ]
        trees_for_classify: dict[str, list[dict]] = {
            slug: taxonomy_trees.get(slug, []) for slug in kbs_with_coverage
        }

        # SPEC-RAG-QUERY-REWRITE-001 + SPEC-RAG-TAXONOMY-001:
        # Combined rewrite + classify in a single LLM call (REQ-5 zero added roundtrip).
        # When trees_for_classify is empty, falls back to plain rewrite.
        # Anti-hallucination guard inside _rewrite_and_classify filters IDs to
        # the union of all valid IDs across the provided KBs (REQ-4).
        # Fail-open: any failure returns (raw_query, [], meta_with_skip_reason).
        (
            rewritten_query,
            classified_node_ids,
            rewrite_meta,
        ) = await _rewrite_and_classify(query, conversation_history, trees_for_classify)
        # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-6: gate raw query content out of
        # the query_rewrite log line in 'off' / 'shadow' mode. Operators keep
        # full-shape diagnostics (timing, was_changed, skip reason) but never
        # see literal customer text.
        telemetry_level = feature.get("telemetry_level", "shadow")
        try:
            if telemetry_level == "full":
                logger.info(
                    "query_rewrite org_id=%s user_id=%s raw_query=%r rewritten_query=%r "
                    "rewrite_ms=%d was_changed=%s skipped=%s",
                    org_id,
                    user_id,
                    query,
                    rewritten_query,
                    rewrite_meta.get("rewrite_ms", 0),
                    rewrite_meta.get("was_changed", False),
                    rewrite_meta.get("skipped", ""),
                )
            else:
                logger.info(
                    "query_rewrite_metadata org_id=%s user_id=%s "
                    "rewrite_ms=%d was_changed=%s skipped=%s",
                    org_id,
                    user_id,
                    rewrite_meta.get("rewrite_ms", 0),
                    rewrite_meta.get("was_changed", False),
                    rewrite_meta.get("skipped", ""),
                )
        except Exception:
            # Logging itself must never abort the hook (REQ-2 fail-open).
            pass

        # SPEC-RAG-TAXONOMY-001 REQ-1+2: inject taxonomy filter iff at least
        # one in-scope KB has coverage AND the classifier returned >= 1 valid
        # node ID. node IDs are globally unique across portal_taxonomy_nodes,
        # so retrieval-api's ANY-of filter is collision-free across KBs.
        taxonomy_applied = False
        taxonomy_skip_reason: str | None = None
        if not TAXONOMY_ENABLED:
            taxonomy_skip_reason = "disabled"
        elif not kbs_in_scope:
            taxonomy_skip_reason = "no_kbs_in_scope"
        elif not kbs_with_coverage:
            taxonomy_skip_reason = "all_kbs_low_coverage"
        elif not classified_node_ids:
            taxonomy_skip_reason = "empty_classify"
        else:
            taxonomy_applied = True

        # REQ-7: log taxonomy_classify event on every classify invocation.
        if TAXONOMY_ENABLED and kbs_in_scope:
            try:
                coverage_repr = ",".join(
                    f"{slug}={taxonomy_coverage_map.get(slug, 0.0):.2f}"
                    for slug in kbs_in_scope
                )
                logger.info(
                    "taxonomy_classify org_id=%s kb_slugs=%s coverage=%s "
                    "kbs_with_coverage=%s classified_node_ids=%s "
                    "was_applied=%s skip_reason=%s",
                    org_id,
                    ",".join(kbs_in_scope),
                    coverage_repr,
                    ",".join(kbs_with_coverage),
                    classified_node_ids,
                    taxonomy_applied,
                    taxonomy_skip_reason,
                )
            except Exception:
                pass

        retrieve_body: dict = {
            "query": rewritten_query,
            "raw_query": query,
            "org_id": org_id,
            # Zitadel sub — matches PortalUser.zitadel_user_id (identity-verify)
            # AND owner_user_id on personal-KB qdrant chunks
            # (klai-portal/backend/app/api/knowledge.py:172-204).
            "user_id": user_id,
            "scope": scope,
            "top_k": RETRIEVE_TOP_K,
            "conversation_history": conversation_history,
            # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-4: forward the per-tenant
            # telemetry mode so retrieval-api can gate its content emission
            # + shadow-store INSERT accordingly. Default 'shadow' on cache
            # miss + portal outage (fail-open per REQ-4).
            "telemetry_level": telemetry_level,
        }
        if kb_slugs_for_request:
            retrieve_body["kb_slugs"] = kb_slugs_for_request
        # REQ-3: inject taxonomy_node_ids only when coverage is sufficient
        # and the classifier produced valid IDs. The org/kb scoping filters
        # are NEVER weakened — they run in addition to this filter.
        if taxonomy_applied:
            retrieve_body["taxonomy_node_ids"] = classified_node_ids

        # SPEC-SEC-SERVICE-AUTH-001 Phase C-1: prefer JWT auth, fall back to
        # legacy X-Internal-Secret on either mint failure OR receiver-side
        # 401/403 (e.g. when Zitadel audience/scope grant for the receiver
        # is not yet wired up). See ``_retrieve_with_dual_auth``.
        t0 = time.monotonic()
        retrieval_failure: str | None = None
        result: dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=RETRIEVE_TIMEOUT) as client:
                resp = await _retrieve_with_dual_auth(client, retrieve_body)
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPStatusError as exc:
            # @MX:WARN: 4xx from /retrieve is a config error, not a transient
            # failure. Surface to the user — silent-degrade hid the
            # caller-service-header regression in production for 7 days.
            # @MX:REASON: SPEC-SEC-IDENTITY-ASSERT-001 Phase D + the
            # `retrieve-caller-service-header-mismatch` pitfall.
            body_snippet = ""
            try:
                body_snippet = exc.response.text[:200]
            except Exception:
                pass
            logger.error(
                "KlaiKnowledgeHook: retrieval HTTP %d — body=%r — failing loud",
                exc.response.status_code,
                body_snippet,
            )
            retrieval_failure = f"HTTP {exc.response.status_code}"
        except Exception as exc:
            # Network errors / timeouts: still log error (was warning), but
            # surface in chat so a sustained outage is impossible to miss.
            logger.error("KlaiKnowledgeHook: retrieval failed (%s) — failing loud", exc)
            retrieval_failure = type(exc).__name__

        if retrieval_failure is not None:
            # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): English
            # instruction to the model; the warning the model emits is in the
            # user's detected language thanks to GROUNDED_CHAT_SYSTEM_PROMPT.
            kb_unavailable_notice = (
                "[Klai Knowledge Base — TEMPORARILY UNAVAILABLE. Answer using "
                "your general knowledge. Begin your answer with a warning to "
                "the user, written in the language you detected from their "
                "most recent substantive message: tell them you could not "
                f"reach the knowledge base (technical reason: {retrieval_failure}), "
                "this answer is therefore not based on their own documentation, "
                "and they should refresh or try again later.]\n"
            )
            prefix = _compose_libre_chat_prefix(templates_block, kb_unavailable_notice)
            _prepend_system_prefix(messages, prefix)
            data["messages"] = messages
            data.setdefault("metadata", {})["_klai_kb_meta"] = {
                "org_id": org_id,
                "user_id": user_id,
                "chunks_injected": 0,
                "retrieval_ms": int((time.monotonic() - t0) * 1000),
                "gate_bypassed": False,
                "retrieval_failure": retrieval_failure,
            }
            return data

        retrieval_ms = int((time.monotonic() - t0) * 1000)

        # If the retrieval-gate determined no KB context is needed, skip injection.
        # Multilingual foundation still applies — REQ-10.
        if result.get("retrieval_bypassed"):
            _prepend_system_prefix(
                messages, _compose_libre_chat_prefix(templates_block)
            )
            data["messages"] = messages
            data.setdefault("metadata", {})["_klai_kb_meta"] = {
                "org_id": org_id,
                "user_id": user_id,
                "chunks_injected": 0,
                "retrieval_ms": retrieval_ms,
                "gate_bypassed": True,
            }
            return data

        chunks = result.get("chunks", [])
        evidence_pack = result.get("evidence_pack")
        has_evidence_pack = isinstance(evidence_pack, dict)
        if not has_evidence_pack:
            logger.error(
                "retrieval_response_missing_evidence_pack org_id=%s user_id=%s chunks=%d",
                org_id,
                user_id,
                len(chunks) if isinstance(chunks, list) else 0,
            )
            _prepend_system_prefix(
                messages, _compose_libre_chat_prefix(templates_block)
            )
            data["messages"] = messages
            original_stream = data.get("stream")
            render_strategy = _select_kb_render_strategy(original_stream)
            if render_strategy.force_non_streaming:
                data["stream"] = False
            data.setdefault("metadata", {})["_klai_kb_meta"] = {
                "org_id": org_id,
                "user_id": user_id,
                "user_query": query,
                "chunks_injected": 0,
                "chunk_ids": [],
                "allowed_source_urls": [],
                "allowed_image_urls": [],
                "citation_source_urls": {},
                "citation_chunks": [],
                "trusted_sources": [],
                "evidence_pack": None,
                "citable_sources_count": 0,
                "no_citable_sources": True,
                "no_citable_reason": "missing_evidence_pack",
                "original_stream": original_stream,
                "render_mode": render_strategy.mode,
                "retrieval_ms": retrieval_ms,
                "gate_bypassed": False,
            }
            return data
        evidence_chunks = _evidence_pack_items_as_chunks(evidence_pack)
        trusted_sources = _normalise_evidence_pack_sources(evidence_pack)
        context_chunks = evidence_chunks
        # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-2: confidence band drives the
        # anti-hallucination injection. None on bypass paths (fail-open).
        confidence_band: str | None = result.get("confidence_band")
        low_confidence_inject = confidence_band in ("low", "unknown")

        # --- Gap detection (KB-014) ---
        gap_type = _classify_gap(chunks)
        if gap_type is not None and org_id and user_id:
            _fire_gap_event(
                org_id=org_id,
                user_id=user_id,
                query_text=query,
                gap_type=gap_type,
                chunks=chunks,
                retrieval_ms=retrieval_ms,
                taxonomy_node_ids=retrieve_body.get("taxonomy_node_ids") or None,
            )

        # --- Retrieval log (SPEC-KB-015-01) ---
        chunk_ids = [c.get("chunk_id") for c in chunks if c.get("chunk_id")]
        reranker_scores = [c.get("reranker_score") or 0.0 for c in chunks]
        if chunk_ids and not result.get("retrieval_bypassed"):
            _fire_retrieval_log(org_id, user_id, chunk_ids, reranker_scores, query)

        if not context_chunks:
            # Zero chunks but templates may still apply. Multilingual
            # foundation still applies — REQ-10.
            _prepend_system_prefix(
                messages, _compose_libre_chat_prefix(templates_block)
            )
            data["messages"] = messages
            if has_evidence_pack:
                original_stream = data.get("stream")
                render_strategy = _select_kb_render_strategy(original_stream)
                if render_strategy.force_non_streaming:
                    data["stream"] = False
                data.setdefault("metadata", {})["_klai_kb_meta"] = {
                    "org_id": org_id,
                    "user_id": user_id,
                    "user_query": query,
                    "chunks_injected": 0,
                    "chunk_ids": [],
                    "allowed_source_urls": [],
                    "allowed_image_urls": [],
                    "citation_source_urls": {},
                    "citation_chunks": [],
                    "trusted_sources": [],
                    "evidence_pack": evidence_pack,
                    "citable_sources_count": 0,
                    "no_citable_sources": True,
                    "no_citable_reason": evidence_pack.get("no_citable_reason")
                    if isinstance(evidence_pack, dict)
                    else None,
                    "original_stream": original_stream,
                    "render_mode": render_strategy.mode,
                    "retrieval_ms": retrieval_ms,
                    "gate_bypassed": False,
                }
            return data

        # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): English instructions
        # to the model. The user-facing answer is in the user's detected
        # language via GROUNDED_CHAT_SYSTEM_PROMPT prepended at the call site.
        # Build context block with provenance labels per chunk.
        # Narrow: model must answer strictly from KB chunks only.
        # Broad (default): KB as additional context, general knowledge allowed.
        if kb_narrow:
            header = (
                "[Klai Knowledge Base — answer strictly using only the sources "
                "below. Do not use general knowledge beyond these sources. "
                "If the answer is not present, say so plainly in the user's "
                "detected language (e.g. 'I cannot find this in the knowledge "
                "base' / 'Dat staat niet in de kennisbank' / 'Das steht nicht "
                "in der Wissensdatenbank').]\n"
            )
        else:
            header = (
                "[Klai Knowledge Base — use this as supplementary context for "
                "your answer. You may complement it with your general "
                "knowledge.]\n"
            )
        source_link_instruction = (
            "[ANSWER FORMAT — always follow this:\n"
            "1. Begin with a short TL;DR (2-3 sentences) of the answer. "
            "Write it as normal prose, not as a Markdown heading. "
            "Use the standard short-summary label in the SAME LANGUAGE as the "
            "user's question — NOT the language of the source documents. "
            "'TL;DR' is universally understood and is a safe default in any "
            "language.\n"
            "2. Do not write source lists, URLs, Markdown links, footnotes, "
            "or citation numbers. The application adds citations after "
            "generation from retrieved metadata.\n"
            "3. Do not preserve source-list step numbers when a retrieved "
            "chunk starts mid-procedure; rewrite steps into a clean sequence.\n"
            "4. If needed for a clear explanation, or if the user asks for "
            "more detail, follow with an extended answer with inline "
            "explanation.\n"
            "   Be concise but complete. No walls of text — write as if you "
            "are helping a colleague.\n\n"
            "STRICT:\n"
            "- NEVER invent or write a URL. No notion.so, no portal.voys.nl, "
            "no guessed documentation paths.\n"
            "- NEVER use placeholder, example, or documentation-only domains.\n"
            "- Never use a title as URL target.\n\n"
            "IMAGES:\n"
            "- Only include image markdown if a chunk below already contains "
            "an explicit ![...](...) image tag.\n"
            "- Change NOTHING about the image URL. Copy the entire "
            "![...](https://...) tag exactly.\n"
            "- NEVER create, guess, search for, or suggest an image URL.\n"
            "- NEVER use placeholder, example, or documentation-only image URLs.\n"
            "- If the user asks for an image and no explicit image tag is "
            "present in the chunks, say plainly that no image is available "
            "in the knowledge base.\n"
            "- Do NOT add images in the TL;DR (section 1).]\n"
        )
        lines = [header, source_link_instruction]
        citation_registry = build_citation_registry(context_chunks)
        citation_source_urls: dict[int, str] = {}
        for chunk_index, chunk in enumerate(context_chunks, 1):
            source_url = _chunk_source_url(chunk)
            if source_url:
                citation_source_urls[chunk_index] = source_url
        allowed_source_urls: set[str] = (
            {source["url"] for source in trusted_sources if isinstance(source.get("url"), str)}
            if has_evidence_pack
            else {source.url for source in citation_registry.sources}
        )
        allowed_image_urls: set[str] = set()

        context_block = render_evidence_context(context_chunks, include_source_urls=False)
        if context_block:
            lines.append(context_block)

        for chunk in context_chunks:
            absolute_urls = [
                url
                for url in (
                    _absolute_image_url(u) for u in chunk.get("image_urls") or []
                )
                if url
            ]
            allowed_image_urls.update(absolute_urls)
            for i, img_url in enumerate(absolute_urls, 1):
                lines.append(f"![afbeelding {i}]({img_url})")
            lines.append("")
        lines.append("[End knowledge base context]")
        # SPEC-RAG-MULTILINGUAL-CHAT-001: KB content is often in a different
        # language than the user's question. Re-affirm the language contract
        # AFTER the KB block so the most-recent instruction wins when Mistral
        # is anchored on the dominant language of the source documents.
        lines.append(
            "[LANGUAGE REMINDER] The knowledge-base chunks above may be in a "
            "different language than the user's question. Always respond in "
            "the language of the user's most recent substantive question, "
            "NOT the language of the source documents. Translate cited "
            "content into the user's language without translator disclaimers."
        )
        # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-2: anti-hallucination layer.
        # Fires when retrieval-api signals confidence_band ∈ {low, unknown}.
        # Most-recent-instruction wins, so it goes AFTER the language reminder.
        # Disabled when KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION=1 (rollback
        # escape hatch — toggle off without redeploying retrieval-api).
        if low_confidence_inject and not _LOW_CONFIDENCE_INJECTION_DISABLED:
            lines.append(_LOW_CONFIDENCE_INJECTION_TEXT)
            # NOTE: warning-level (not info) is deliberate. The litellm
            # container's root logger filters info-level emits from
            # non-uvicorn modules (verified 2026-05-08 via VictoriaLogs:
            # zero info-level klai_knowledge events visible despite
            # the code path being reached). Other warning-level events
            # in this module (`KlaiKnowledgeHook: jwt rejected ...`,
            # `query_rewrite_and_classify_failed`) DO surface, so the
            # injection event uses the same level for parity with
            # observable hook events. Lets operators query
            # `service:litellm AND _msg:"low_confidence_injection_applied"`
            # for incident triage on a per-request_id basis.
            logger.warning(
                "low_confidence_injection_applied org_id=%s confidence_band=%s chunks_injected=%d",
                org_id,
                confidence_band,
                len(context_chunks),
            )
        context_block = "\n".join(lines)

        # SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-HOOK-E1: templates → KB → existing.
        # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): GROUNDED_CHAT_SYSTEM_PROMPT
        # leads — same contract as paths B (partner_chat) and C (retrieval-api /chat).
        prefix = _compose_libre_chat_prefix(templates_block, context_block)
        _prepend_system_prefix(messages, prefix)
        data["messages"] = messages
        original_stream = data.get("stream")
        render_strategy = _select_kb_render_strategy(original_stream)
        if render_strategy.force_non_streaming:
            data["stream"] = False
        # Signal KB injection to downstream hooks (e.g. custom_router, post-call logger)
        # Stored in data["metadata"] so it is never forwarded to the LLM provider.
        data.setdefault("metadata", {})["_klai_kb_meta"] = {
            "org_id": org_id,
            "user_id": user_id,
            "user_query": query,
            "chunks_injected": len(context_chunks),
            "chunk_ids": [c.get("chunk_id") for c in context_chunks if c.get("chunk_id")],
            "allowed_source_urls": sorted(allowed_source_urls),
            "allowed_image_urls": sorted(allowed_image_urls),
            "citation_source_urls": {
                str(index): url for index, url in citation_source_urls.items()
            },
            "citation_chunks": context_chunks,
            "trusted_sources": trusted_sources,
            "evidence_pack": evidence_pack if isinstance(evidence_pack, dict) else None,
            "citable_sources_count": len(trusted_sources)
            if has_evidence_pack
            else len(citation_registry.sources),
            "original_stream": original_stream,
            "render_mode": render_strategy.mode,
            "retrieval_ms": retrieval_ms,
            "gate_bypassed": False,
        }
        return data

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        kb_meta = data.get("metadata", {}).get("_klai_kb_meta")
        if kb_meta and not kb_meta.get("gate_bypassed"):
            stats = _compose_non_streaming_kb_response(response, kb_meta)
            _log_kb_citation_render(kb_meta, stats, stream=False)
            logger.info(
                "KB injection: org=%s user=%s chunks=%d retrieval_ms=%d",
                kb_meta["org_id"],
                kb_meta["user_id"],
                kb_meta["chunks_injected"],
                kb_meta["retrieval_ms"],
            )
        return response

    async def async_post_call_streaming_iterator_hook(self, user_api_key_dict, response, request_data):
        kb_meta = request_data.get("metadata", {}).get("_klai_kb_meta")
        if (
            not kb_meta
            or kb_meta.get("gate_bypassed")
            or not _is_streaming_kb_render_mode(kb_meta.get("render_mode"))
        ):
            async for item in response:
                yield item
            return

        pending_item = None
        async for item in response:
            if pending_item is not None:
                yield pending_item
            pending_item = item
            stats = _compose_streaming_kb_response(item, kb_meta)
            _log_kb_citation_render(kb_meta, stats, stream=True)
        if pending_item is not None and kb_meta.get("_citation_stream_parts"):
            stats = _compose_streaming_kb_response(pending_item, kb_meta, flush_stream=True)
            _log_kb_citation_render(kb_meta, stats, stream=True)
        if pending_item is not None:
            yield pending_item

    async def async_post_call_failure_hook(self, *args, **kwargs):
        pass


# Module-level instance (some LiteLLM versions require this form)
klai_knowledge_hook = KlaiKnowledgeHook()
