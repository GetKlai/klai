"""KlaiPiiEnforcer — Phase 3 PII mask/map/restore for the Mistral call path.

SPEC-PRIVACY-MISTRAL-PII-001 Phase 3 (REQ-7 through REQ-11). Shipped INERT:
gated end-to-end behind ``KLAI_PII_ENFORCE`` (default OFF). With the flag
off, ``async_pre_call_hook`` returns its input completely unchanged — same
object, no analyzer call, no map entry, no verbatim-instruction injection —
so this module changes nothing about production traffic until the flag is
deliberately flipped. That is a requirement with its own test class in
``tests/test_pii_enforce.py``, not an incidental property.

Activation hardening, added after Phase 3 shipped inert: ``KLAI_PII_ENFORCE``
alone applies to every org identically the moment it flips, and this stack
has never run end to end against a real Mistral stream. ``async_pre_call_hook``
now also checks ``KLAI_PII_ENFORCE_ORG_IDS`` (see ``_org_is_enforced``) so the
first activation can be scoped to one org rather than the whole tenant base.
Both variables must be set for enforcement to do anything for any request —
an empty or unset allowlist is "enforce nobody", not "enforce everybody".

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
from typing import Any, NamedTuple

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


def _parse_org_allowlist(value: str) -> frozenset[str]:
    return frozenset(v.strip() for v in value.split(",") if v.strip())


# ---------------------------------------------------------------------------
# Activation hardening — per-org enforcement scoping
# ---------------------------------------------------------------------------
# KLAI_PII_ENFORCE alone is read once at import time and applies identically
# to every tenant behind one process-wide boolean: the first activation
# would hit every customer at once, and this stack has never run end to end
# against a real Mistral stream (all 820 tests mock the analyzer and the
# stream). KLAI_PII_ENFORCE_ORG_IDS is a comma-separated allowlist of
# org_ids that enforcement actually applies to, so the first activation can
# be exactly one org while the rest of the tenant base is provably
# untouched -- wired into deploy/docker-compose.yml next to
# KLAI_PII_ENFORCE.
KLAI_PII_ENFORCE_ORG_IDS = _parse_org_allowlist(os.getenv("KLAI_PII_ENFORCE_ORG_IDS", ""))


def _org_is_enforced(org_id: Any) -> bool:
    """Whether masking/restore should run at all for THIS request's org.

    Two deliberate decisions, each argued rather than assumed:

    1. EMPTY allowlist + KLAI_PII_ENFORCE=true means enforcement for NO
       org -- not "every org", which would be the reading that reproduces
       exactly the all-or-nothing activation this mechanism exists to
       remove. "All orgs" is the dangerous reading: a bare
       `KLAI_PII_ENFORCE=true` with `KLAI_PII_ENFORCE_ORG_IDS` merely
       unset (easy to do by accident -- a new env var nobody set yet, not
       a deliberate empty override) would silently enforce for every
       tenant with no pilot phase at all, which is precisely the failure
       mode named in the SPEC's own Risks table ("Fail-closed turns a
       Presidio outage into a chat outage" -- the activation-blast-radius
       twin of that risk). "No orgs" instead fails toward inert, which is
       the direction every other default in this stack already fails:
       KLAI_PII_ENFORCE itself defaults off, REQ-7's optional per-entity
       policy defaults off per org, and REQ-10 only fails closed once the
       control is already known to be active. Turning enforcement on for
       the first org is therefore a deliberate TWO-variable action --
       flip KLAI_PII_ENFORCE AND name that org -- never a one-flag flip
       that silently waits on a second variable nobody remembered.
    2. A request with NO org_id (the widget/partner master-key path --
       `klai_knowledge.py:481-483` skips it for the identical reason) is
       NEVER enforced, regardless of the flag or the allowlist's
       contents. The allowlist matches org IDENTITIES; a request that
       carries none has nothing to match, and treating "no identity" as
       "matches everything" would be the same all-or-nothing mistake as
       (1) applied to a path this module cannot attribute to a specific
       tenant for audit in the first place. This mirrors
       `klai_pii_org_policy.py`'s own fail-closed-to-`EMPTY_POLICY`
       handling of a missing org_id: masking fails closed here the same
       way per-entity policy resolution already does. Concretely, this
       means REQ-7's "SECRET and NL_BSN are MASK for every org,
       unconditionally" is scoped BY this allowlist during a rollout, the
       same as every other entity -- there is no path left that masks
       unconditionally across the whole tenant base while the allowlist
       is not yet "every org", which is what makes the rollout actually
       gradual rather than gradual-except-for-credentials-and-BSN.
    """
    if not org_id:
        return False
    return org_id in KLAI_PII_ENFORCE_ORG_IDS


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

# ---------------------------------------------------------------------------
# Length cap on analysed text — system-review finding M4
# ---------------------------------------------------------------------------
# presidio-analyzer runs with `cpus: '1'` (docker-compose.yml), shared by
# every tenant. Before this, no code anywhere capped how much text a single
# `/analyze` call could carry: the PEM pattern's own quadratic-on-unmatched-
# markers cost (fixed separately in klai_pii_recognizers.py, bounding the
# body to 5000 chars) was one way a single oversized paste could peg that
# shared core; a length cap is defense in depth against that class of
# problem for every recognizer, not just the one that was measured, and
# bounds each individual HTTP call's cost regardless.
#
# 20,000 chars: double the NFR's own reference payload size ("p95 under 60ms
# added per request for a 10,000-character payload", this SPEC's Non-
# Functional Requirements section) — generous headroom above the size the
# latency budget is defined against, while still keeping a single `/analyze`
# call's regex-and-checksum work bounded and cheap.
#
# Unlike klai_pii_observe.py (Phase 2, read-only, fail-open — truncating the
# measured text only under-counts a telemetry signal), this module masks
# what actually reaches Mistral. REQ-10's fail-closed contract means
# ANALYSING less than the full outbound text and then forwarding the
# unanalysed remainder unmasked is not an option — that is unminimised
# content reaching the provider, exactly what REQ-10 forbids. So text longer
# than the cap is NOT truncated: it is split into overlapping windows
# (`_chunk_windows`), every window is analysed, and each detected span is
# attributed to exactly the one window whose "core" region contains its
# start offset — see `_analyze_spans_chunked` for the algorithm. 100% of the
# outbound text is always analysed; the cap only bounds how much of it goes
# into any single `/analyze` HTTP call.
#
# Klai supports genuinely large single-message payloads by design — chat
# attachments extract up to `KLAI_CHAT_PDF_MAX_EXTRACTED_TOKENS` (120,000
# tokens, docker-compose.yml) of PDF text directly into a message's content.
# Refusing any request whose text exceeds this cap (the other REQ-10-
# compatible option) would make PII enforcement incompatible with that
# already-shipped capability the moment an org opts in — a worse regression
# than the extra HTTP round trips chunking costs.
_MAX_ANALYZE_CHARS = 20_000

# Overlap between adjacent analysed windows must exceed the longest entity
# this pack can ever match, or an entity straddling a window boundary could
# be truncated in both windows and detected in neither. The longest bounded
# entity is the PEM private-key block: `_PEM_MAX_BODY_CHARS` (5000, see
# klai_pii_recognizers.py) plus ~40 chars of BEGIN/END marker text. 6000
# gives comfortable margin above that ~5040 ceiling.
#
# Residual, explicitly not covered: the SECRET patterns for JWTs, Bearer
# tokens and provider-key prefixes use open-ended quantifiers
# (`{10,}`/`{16,}`) with no upper bound, so a single credential-shaped value
# longer than this overlap could in principle straddle a boundary
# undetected. A real-world JWT/Bearer/API key of that length (>6000 chars in
# one token) is not a realistic shape Klai has ever observed; this is a
# known, documented bound rather than a claim of zero risk.
_CHUNK_OVERLAP_CHARS = 6_000

# Bounds TOTAL concurrent /analyze HTTP calls in flight for one request,
# across both `_mask_messages`'s per-text-unit gather AND
# `_analyze_spans_chunked`'s per-window gather. Without this, one huge
# multi-message payload (several large tool-call arguments, each itself
# chunked into a dozen+ windows) can fan out into dozens of concurrent calls
# against the single-core analyzer — the same shared-resource risk the
# length cap exists to bound, just reached via concurrency instead of one
# oversized call.
_MAX_CONCURRENT_ANALYZER_CALLS = 8
_analyzer_call_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_ANALYZER_CALLS)

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


async def _analyze_spans_single(
    http: httpx.AsyncClient, text: str, language: str
) -> list[DetectedSpan]:
    """One `/analyze` HTTP call over `text` as-is, no chunking.

    Callers are responsible for keeping `text` at or under
    `_MAX_ANALYZE_CHARS` — this function does not check. Bounded by
    `_analyzer_call_semaphore` so a single request's fan-out (multiple text
    units, each possibly chunked) cannot flood the single-core analyzer with
    unbounded concurrent calls.
    """
    url = PRESIDIO_ANALYZER_API_BASE.rstrip("/") + "/analyze"
    async with _analyzer_call_semaphore:
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


# NamedTuple, not @dataclass — this is not a style choice.
#
# Production incident 2026-08-21: a module-level `@dataclass` here
# crashlooped litellm on startup with
# `AttributeError: 'NoneType' object has no attribute '__dict__'`.
# LiteLLM loads modules named in `callbacks:` via
# `spec_from_file_location` + `exec_module` WITHOUT inserting them into
# `sys.modules` (litellm/proxy/types_utils/utils.py::get_instance_fn). With
# `from __future__ import annotations` every annotation is a string, so
# `dataclasses._process_class` calls `_is_type`, which does
# `sys.modules.get(cls.__module__).__dict__` — and that is None.
#
# NamedTuple resolves annotations without consulting sys.modules, so it is
# immune. `tests/test_callback_module_loading.py` pins this by importing
# every callbacks-registered module through the real loader; it was RED on
# the dataclass version and is GREEN on this one.
class _ChunkWindow(NamedTuple):
    """One analysed window over a longer text.

    `core_start`/`core_end` partition the FULL text exactly, with no gap and
    no overlap between windows — every absolute offset belongs to exactly
    one window's core. `window_start`/`window_end` is the (padded, may
    overlap neighbours) slice actually sent to `/analyze`: padding by
    `_CHUNK_OVERLAP_CHARS` on each side guarantees that any entity whose
    span STARTS inside this window's core is fully contained in the
    analysed window, even if the entity's end reaches past the core
    boundary — so it is both detected in this window and unambiguously
    owned by it (see `_analyze_spans_chunked`).
    """

    core_start: int
    core_end: int
    window_start: int
    window_end: int


def _chunk_windows(text_len: int) -> list[_ChunkWindow]:
    """Partition `[0, text_len)` into cores, each padded into a window.

    Sol-review finding: an earlier version sized the CORE at
    `_MAX_ANALYZE_CHARS` and then padded both sides by
    `_CHUNK_OVERLAP_CHARS`, so an interior window's actual analysed size was
    `_MAX_ANALYZE_CHARS + 2 * _CHUNK_OVERLAP_CHARS` (32,000 chars at the
    production 20,000/6,000 values) — well past the size the module-level
    comment above `_MAX_ANALYZE_CHARS` promises ("bounds how much of it goes
    into any single `/analyze` HTTP call"). The core is now sized so the
    PADDED window itself never exceeds `_MAX_ANALYZE_CHARS`: `core_size =
    _MAX_ANALYZE_CHARS - 2 * _CHUNK_OVERLAP_CHARS`. The correctness argument
    (an entity starting in a core is always fully visible in that core's
    padded window) only requires `_CHUNK_OVERLAP_CHARS` to exceed the
    longest matchable entity — it does not depend on how big the core is —
    so shrinking the core changes nothing about detection correctness, only
    how many windows a long text is split into.
    """
    if text_len <= _MAX_ANALYZE_CHARS:
        return [_ChunkWindow(0, text_len, 0, text_len)]
    # Guard against a misconfiguration where the overlap alone (padded on
    # both sides) would already reach or exceed the cap, which would leave
    # no room for a core and loop forever (core_start never advancing).
    core_size = max(1, _MAX_ANALYZE_CHARS - 2 * _CHUNK_OVERLAP_CHARS)
    windows: list[_ChunkWindow] = []
    core_start = 0
    while core_start < text_len:
        core_end = min(text_len, core_start + core_size)
        window_start = max(0, core_start - _CHUNK_OVERLAP_CHARS)
        window_end = min(text_len, core_end + _CHUNK_OVERLAP_CHARS)
        windows.append(_ChunkWindow(core_start, core_end, window_start, window_end))
        core_start = core_end
    return windows


async def _analyze_spans_chunked(
    http: httpx.AsyncClient, text: str, language: str
) -> list[DetectedSpan]:
    """Analyse text longer than `_MAX_ANALYZE_CHARS` with full coverage.

    REQ-10's fail-closed contract forbids analysing less than the whole
    outbound text and forwarding the rest unmasked — see the module-level
    comment above `_MAX_ANALYZE_CHARS`. Every character of `text` is
    covered by exactly one window's CORE region (`_chunk_windows`), and
    each window is padded by `_CHUNK_OVERLAP_CHARS` so an entity starting
    in that core is never truncated at the analysed-window edge. A span is
    kept only if its absolute start falls inside the window that owns it —
    this is what prevents the same entity from being counted twice out of
    two overlapping windows.
    """
    windows = _chunk_windows(len(text))
    per_window_spans = await asyncio.gather(
        *(
            _analyze_spans_single(http, text[w.window_start : w.window_end], language)
            for w in windows
        )
    )

    spans: list[DetectedSpan] = []
    for window, window_spans in zip(windows, per_window_spans):
        for span in window_spans:
            abs_start = span.start + window.window_start
            abs_end = span.end + window.window_start
            if window.core_start <= abs_start < window.core_end:
                spans.append(
                    DetectedSpan(
                        entity_type=span.entity_type,
                        start=abs_start,
                        end=abs_end,
                        score=span.score,
                    )
                )
    return spans


async def _analyze_spans(http: httpx.AsyncClient, text: str, language: str) -> list[DetectedSpan]:
    """Dispatch to a single `/analyze` call, or to chunked analysis above
    `_MAX_ANALYZE_CHARS`. See the module-level comment above
    `_MAX_ANALYZE_CHARS` for why chunking, not truncation, is the enforce-
    path answer to an oversized payload.
    """
    if len(text) <= _MAX_ANALYZE_CHARS:
        return await _analyze_spans_single(http, text, language)
    return await _analyze_spans_chunked(http, text, language)


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

    Per-org scoping (`KLAI_PII_ENFORCE_ORG_IDS`, see `_org_is_enforced`)
    is checked ONLY in `async_pre_call_hook`, the one place org_id is
    available and the one place masking actually happens. The post-call
    hooks below (`async_post_call_success_hook`,
    `async_post_call_streaming_iterator_hook`,
    `async_post_call_failure_hook`) do not re-check it: they are purely
    map-driven — restore a placeholder if `_pii_map_store` has an entry
    for this `litellm_call_id`, otherwise no-op — and an org excluded from
    the allowlist never gets a map entry written for it in the first
    place, so the post-call hooks are already a correct no-op for that
    org without needing their own copy of the same check.
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
        if not _org_is_enforced(org_id):
            # Activation hardening: KLAI_PII_ENFORCE is on globally, but
            # this org (or a request with no org_id at all) is not in
            # KLAI_PII_ENFORCE_ORG_IDS -- see _org_is_enforced's docstring
            # for why empty/missing means "not enforced", not "enforced for
            # everyone". No analyzer call, no map entry, byte-identical
            # passthrough, same as the flag being off entirely.
            return data
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
