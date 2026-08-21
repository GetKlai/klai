"""KlaiPiiEnforcer — Phase 3 PII mask/map/restore for the Mistral call path.

SPEC-PRIVACY-MISTRAL-PII-001 Phase 3 (REQ-7 through REQ-11). Shipped INERT:
gated end-to-end behind ``KLAI_PII_ENFORCE`` (default OFF). With the flag
off, ``async_pre_call_hook`` returns its input completely unchanged — same
object, no analyzer call, no map entry, no verbatim-instruction injection —
so this module changes nothing about production traffic until the flag is
deliberately flipped. That is a requirement with its own test class in
``tests/test_pii_enforce.py``, not an incidental property.

Why Klai owns mask/map/restore instead of ``output_parse_pii`` (REQ-0a):
Phase 0 measured the native LiteLLM Presidio guardrail returning an EMPTY
token map on the streaming path (the second failure shape in
BerriAI/litellm#6247 — the map does not survive from the pre-call hook to
the post-call hook), and both Klai chat paths stream. So this module never
uses the native guardrail's restore. It calls presidio-analyzer's
``/analyze`` directly, does its own typed/numbered placeholder substitution
(``klai_pii_text_masking.py``), and restores from its own process-local map
(``klai_pii_map_store.py``) in ``async_post_call_success_hook`` /
``async_post_call_streaming_iterator_hook`` — the same buffered
chunk-rewrite family of hook already working at
``klai_knowledge.py:1667-1703`` for the citation footer.

Callback registration order (config.yaml): registered as the LAST entry in
``litellm_settings.callbacks``, after ``klai_pii_observe.klai_pii_observer``.
Verified directly against the installed ``litellm==1.96.2`` package
(``litellm/proxy/utils.py``), not assumed from the SPEC's own claim:

- ``async_post_call_streaming_iterator_hook``: ``ProxyLogging`` builds an
  ordered ``iterator_overrides`` list by walking ``litellm.callbacks`` in
  REGISTRATION order (no guardrail/non-guardrail split for this hook, unlike
  the success hook below). It then wraps ``current_response`` through each
  callback's iterator hook in that same order — callback 0 wraps the raw
  upstream stream, callback 1 wraps callback 0's generator, and so on. The
  LAST-registered callback therefore produces the outermost generator, the
  one the proxy actually iterates and streams to the client. Last
  registered = last to see (and last to transform) every chunk = its output
  is what the user sees.
- ``async_post_call_success_hook``: callbacks are split into
  ``guardrail_callbacks`` (``CustomGuardrail`` instances) and
  ``other_callbacks`` (plain ``CustomLogger`` instances, which is what this
  module and ``klai_knowledge_hook`` both are); all guardrails run before
  all other_callbacks regardless of interleaved registration position, but
  WITHIN ``other_callbacks`` the relative registration order is preserved,
  and each callback's non-``None`` return value replaces ``response`` for
  the next one in line. Registering this module last among the
  ``CustomLogger`` entries means it restores the FINAL rendered text (after
  ``klai_knowledge_hook``'s own citation rendering), which is what the user
  actually receives.

Registering this module's pre-call masking AFTER
``klai_pii_observe.klai_pii_observer`` (rather than before) is also
deliberate for a second reason, independent of the ordering facts above:
pre-call hooks run in strict registration order and each hook's return
value becomes the next hook's input ``data``. Placing the masking hook
after the Phase 2 observer means the observer keeps measuring the
pre-mask payload even once enforcement is enabled for individual orgs —
its telemetry stays meaningful instead of measuring its own masked
placeholders. (This module's own docstring elsewhere in the SPEC assumes
the Phase 2 observer is deleted in the Phase 3 PR; this change deliberately
does not delete it, per this PR's explicit hard constraint not to touch
other existing modules — see the implementation report.)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx
from litellm.integrations.custom_logger import CustomLogger

from klai_pii_entities import NEVER_RESTORE_ENTITIES, effective_enabled_entities
from klai_pii_map_store import PiiMapStore
from klai_pii_org_policy import resolve_org_entity_policy
from klai_pii_restore_eval import VERBATIM_TOKEN_SYSTEM_INSTRUCTION
from klai_pii_text_masking import DetectedSpan, mask_text, restore_text, split_safe_tail

logger = logging.getLogger(__name__)


def _truthy_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


# REQ-7/REQ-10: the single switch that keeps this module inert. Read at
# import time (same pattern as klai_pii_observe.py's own env constants) so
# tests reload the module after monkeypatching the env var.
KLAI_PII_ENFORCE = _truthy_env("KLAI_PII_ENFORCE", "false")

# Same internal service name the Phase 0/1/2 code already uses.
PRESIDIO_ANALYZER_API_BASE = os.getenv(
    "PRESIDIO_ANALYZER_API_BASE", "http://presidio-analyzer:3000"
)

_ANALYZER_CALL_TIMEOUT_SECONDS = float(os.getenv("KLAI_PII_ANALYZER_TIMEOUT_SECONDS", "3.0"))
_HTTPX_CLIENT_TIMEOUT_SECONDS = float(os.getenv("KLAI_PII_HTTPX_TIMEOUT_SECONDS", "5.0"))

# REQ-2: Phase 3's own entities are all regex-plus-checksum, registered
# across every language the analyzer serves — this call only needs a
# language the analyzer accepts (PERSON is the only language-sensitive
# entity, and PERSON is never in `enabled_entities`; see
# klai_pii_entities.py). "en" mirrors the Phase 0/2 default.
_ANALYZER_LANGUAGE = os.getenv("KLAI_PII_ANALYZER_LANGUAGE", "en")

_pii_map_store = PiiMapStore()


class PiiAnalyzerUnavailable(RuntimeError):
    """Raised from async_pre_call_hook when enforcement is ON and the
    analyzer errored or timed out. REQ-10: the request SHALL fail rather
    than proceed unminimised. Deliberately a plain RuntimeError subclass —
    fastapi is a `proxy` extra of litellm, not a base dependency, and this
    module must import cleanly under the bare `litellm==1.96.2` package the
    CI test job installs (`.github/workflows/litellm-tests.yml`). LiteLLM's
    own pre-call hook contract ("raise exception if invalid") only requires
    SOME exception to propagate for the request to fail; it does not
    require a particular exception type.
    """


def _org_id_from_key(user_api_key_dict: Any) -> Any:
    metadata = getattr(user_api_key_dict, "metadata", {}) or {}
    return metadata.get("org_id")


# ---------------------------------------------------------------------------
# Text-unit discovery — every place outbound PII-bearing text can live
# ---------------------------------------------------------------------------
def _iter_text_units(messages: list[Any]):
    """Yield (current_text, setter) for every text-bearing location.

    Covers string content, multi-part (list-of-parts) content, and
    tool_calls[].function.arguments — the last one for the same reason
    klai_pii_observe.py scans it (Sol delta-review finding): an assistant
    turn in an agentic flow can carry content=None while tool_calls holds
    an email address or a BSN, and the router forwards those turns to
    Mistral verbatim.
    """
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content:

            def _set_content(new_text: str, _message: dict = message) -> None:
                _message["content"] = new_text

            yield content, _set_content
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "text":
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:

                    def _set_part(new_text: str, _part: dict = part) -> None:
                        _part["text"] = new_text

                    yield text, _set_part
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str) and arguments:

                def _set_arguments(new_text: str, _function: dict = function) -> None:
                    _function["arguments"] = new_text

                yield arguments, _set_arguments


async def _analyze_spans(http: httpx.AsyncClient, text: str, language: str) -> list[DetectedSpan]:
    url = PRESIDIO_ANALYZER_API_BASE.rstrip("/") + "/analyze"
    response = await asyncio.wait_for(
        http.post(url, json={"text": text, "language": language}),
        timeout=_ANALYZER_CALL_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    results = response.json()
    if not isinstance(results, list):
        raise TypeError("presidio-analyzer /analyze returned a non-list response")

    spans: list[DetectedSpan] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        entity_type = item.get("entity_type")
        start = item.get("start")
        end = item.get("end")
        score = item.get("score", 0.0)
        if not isinstance(entity_type, str) or not isinstance(start, int) or not isinstance(end, int):
            continue
        if end <= start:
            continue
        spans.append(
            DetectedSpan(
                entity_type=entity_type,
                start=start,
                end=end,
                score=float(score) if isinstance(score, (int, float)) else 0.0,
            )
        )
    return spans


async def _mask_messages(
    http: httpx.AsyncClient,
    messages: list[Any],
    *,
    enabled_entities: frozenset[str],
    language: str,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Mask every enabled entity across every text unit in ``messages``.

    Analyzer calls run concurrently (asyncio.gather — never `await` inside a
    loop for independent I/O), but masking itself — and therefore instance
    numbering — is applied in a deterministic, single-threaded pass over
    ``units`` afterwards, so numbering never depends on network completion
    order.
    """
    units = list(_iter_text_units(messages))
    if not units:
        return {}, ()

    spans_per_unit = await asyncio.gather(
        *(_analyze_spans(http, text, language) for text, _ in units)
    )

    instance_counters: dict[str, int] = {}
    combined_restore_map: dict[str, str] = {}
    all_masked_types: list[str] = []
    for (text, setter), spans in zip(units, spans_per_unit):
        result = mask_text(
            text,
            spans,
            enabled_entities=enabled_entities,
            never_restore_entities=NEVER_RESTORE_ENTITIES,
            instance_counters=instance_counters,
        )
        if result.masked_entity_types:
            setter(result.masked_text)
            combined_restore_map.update(result.restore_map)
            all_masked_types.extend(result.masked_entity_types)
    return combined_restore_map, tuple(all_masked_types)


# ---------------------------------------------------------------------------
# Response-object accessors — tolerant of both dict and attribute style
# ---------------------------------------------------------------------------
def _get_choices(obj: Any) -> list[Any]:
    if obj is None:
        return []
    choices = obj.get("choices") if isinstance(obj, dict) else getattr(obj, "choices", None)
    return list(choices) if choices else []


def _get_field(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _get_content(obj: Any) -> str | None:
    content = _get_field(obj, "content")
    return content if isinstance(content, str) else None


def _set_content(obj: Any, value: str) -> None:
    if obj is None:
        return
    if isinstance(obj, dict):
        obj["content"] = value
    else:
        obj.content = value


class KlaiPiiEnforcer(CustomLogger):
    """Mask outbound PII (pre-call), restore it in the response (post-call).

    Every method starts with the same `if not KLAI_PII_ENFORCE:` early
    return — the flag is checked independently in each hook rather than
    once at class-construction time, because the class is instantiated once
    at import time (module-level singleton, matching every other hook in
    this proxy) while tests need to flip the flag per test via module
    reload; reading the module-level constant fresh in each method call
    keeps behaviour correct under that reload pattern.
    """

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any]:
        if not KLAI_PII_ENFORCE:
            return data

        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return data

        org_id = _org_id_from_key(user_api_key_dict)
        org_policy = await resolve_org_entity_policy(org_id)
        enabled_entities = effective_enabled_entities(org_policy)

        try:
            async with httpx.AsyncClient(timeout=_HTTPX_CLIENT_TIMEOUT_SECONDS) as http:
                restore_map, masked_types = await _mask_messages(
                    http, messages, enabled_entities=enabled_entities, language=_ANALYZER_LANGUAGE
                )
        except Exception as exc:
            # REQ-10: enforcement ON + analyzer unreachable/erroring -> the
            # request SHALL fail rather than proceed unminimised. Logged at
            # warning with org and call_type, then re-raised so LiteLLM's
            # pre-call hook chain aborts the request.
            logger.warning(
                "pii_enforce_analyzer_failed org_id=%s call_type=%s error=%s",
                org_id,
                call_type,
                exc,
            )
            raise PiiAnalyzerUnavailable(
                "PII analyzer unavailable; refusing to forward an unminimised payload"
            ) from exc

        if not masked_types:
            return data

        call_id = data.get("litellm_call_id")
        if restore_map and call_id:
            _pii_map_store.put(call_id, restore_map)

        # REQ-0b: mandatory whenever masking is active — measured to take
        # PHONE_NUMBER survival from 58.3% to 95.8%. Reusing the EXACT
        # instruction text Phase 0 measured, not a re-translation, so the
        # measured effect actually applies to what gets sent.
        data["messages"] = [
            {"role": "system", "content": VERBATIM_TOKEN_SYSTEM_INSTRUCTION},
            *messages,
        ]
        return data

    async def async_post_call_success_hook(
        self, data: dict[str, Any], user_api_key_dict: Any, response: Any
    ) -> Any:
        if not KLAI_PII_ENFORCE:
            return None

        call_id = data.get("litellm_call_id")
        restore_map = _pii_map_store.get(call_id) if call_id else None
        if call_id:
            _pii_map_store.discard(call_id)  # REQ-11: deleted on the success path
        if not restore_map:
            return None

        for choice in _get_choices(response):
            message = _get_field(choice, "message")
            content = _get_content(message)
            if content:
                _set_content(
                    message,
                    restore_text(content, restore_map, never_restore_entities=NEVER_RESTORE_ENTITIES),
                )
        return response

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict: Any, response: Any, request_data: dict[str, Any]
    ):
        if not KLAI_PII_ENFORCE:
            async for item in response:
                yield item
            return

        call_id = request_data.get("litellm_call_id")
        restore_map = _pii_map_store.get(call_id) if call_id else None
        if not restore_map:
            # Nothing was masked for this call (no PII detected, enforcement
            # was off at mask time, or the entry already expired) -- stream
            # through unchanged. Still clean up defensively: REQ-11 requires
            # deletion at stream end regardless of whether there was
            # anything to restore.
            try:
                async for item in response:
                    yield item
            finally:
                if call_id:
                    _pii_map_store.discard(call_id)
            return

        # Per-choice-index buffers -- REQ-8's tail holdback, sized in
        # klai_pii_text_masking.TAIL_LEN. One-item lookahead (same shape as
        # klai_knowledge.py:1667-1703's citation-footer pattern): every item
        # is yielded exactly once, after we know whether a later item (or
        # stream end) follows it, so the truly-last item can absorb
        # whatever text is still held back at stream end.
        buffers: dict[int, str] = {}
        pending_item: Any = None
        try:
            async for item in response:
                for idx, choice in enumerate(_get_choices(item)):
                    delta = _get_field(choice, "delta")
                    if delta is None:
                        continue
                    content = _get_content(delta) or ""
                    buffered = buffers.get(idx, "") + content
                    safe, buffers[idx] = split_safe_tail(
                        buffered, restore_map, NEVER_RESTORE_ENTITIES
                    )
                    _set_content(delta, safe)
                if pending_item is not None:
                    yield pending_item
                pending_item = item

            if pending_item is not None:
                for idx, choice in enumerate(_get_choices(pending_item)):
                    delta = _get_field(choice, "delta")
                    leftover = buffers.get(idx, "")
                    if delta is not None and leftover:
                        restored_tail = restore_text(
                            leftover, restore_map, never_restore_entities=NEVER_RESTORE_ENTITIES
                        )
                        existing = _get_content(delta) or ""
                        _set_content(delta, existing + restored_tail)
                    buffers[idx] = ""
                yield pending_item
        finally:
            if call_id:
                _pii_map_store.discard(call_id)  # REQ-11: success AND error path

    async def async_post_call_failure_hook(
        self,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: str | None = None,
    ) -> None:
        if not KLAI_PII_ENFORCE:
            return None
        call_id = request_data.get("litellm_call_id") if isinstance(request_data, dict) else None
        if call_id:
            _pii_map_store.discard(call_id)  # REQ-11: deleted on the error path
        return None


klai_pii_enforcer = KlaiPiiEnforcer()
