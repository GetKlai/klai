"""Citation render-mode policy for path-A KB chat."""

from __future__ import annotations

from dataclasses import dataclass

KB_RENDER_MODE_STREAMING_GUARD = "streaming_guard"
KB_RENDER_MODE_LEGACY_STREAMING_GUARD = "legacy_stream_guard"
KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING = "deterministic_non_streaming"
KB_STREAMING_RENDER_MODES = {
    KB_RENDER_MODE_STREAMING_GUARD,
    KB_RENDER_MODE_LEGACY_STREAMING_GUARD,
}


@dataclass(frozen=True)
class KbCitationRenderStrategy:
    mode: str
    force_non_streaming: bool = False


def resolve_kb_render_mode(value: object) -> str:
    """Resolve the configured citation rendering strategy."""
    requested = value.strip().lower() if isinstance(value, str) else ""
    if requested == KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING:
        return KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING
    return KB_RENDER_MODE_STREAMING_GUARD


def is_streaming_kb_render_mode(value: object) -> bool:
    return value in KB_STREAMING_RENDER_MODES


def select_kb_render_strategy(
    original_stream: object,
    *,
    configured_mode: object,
    org_id: object = None,
    non_streaming_org_ids: frozenset[str] | set[str] = frozenset(),
) -> KbCitationRenderStrategy:
    """How this turn's KB answer is rendered.

    An organisation on ``non_streaming_org_ids`` renders in one piece, which is
    what makes the grounding repair reachable (klai_answer_grounding): a
    streamed answer has already been read by the time the check could speak. It
    costs that reader the tokens appearing one by one, a median 4.0 s and 8.3 s
    in the slowest tenth (measured 2026-09-18). Per organisation rather than
    globally, because of five tenants with internal traffic in the thirty days
    to that date one had 169 cited answers and three had none: a single switch
    would take streaming from all of them to serve one.
    """
    if org_id is not None and str(org_id) in non_streaming_org_ids:
        return KbCitationRenderStrategy(
            mode=KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING,
            force_non_streaming=True,
        )
    if original_stream is True:
        return KbCitationRenderStrategy(mode=KB_RENDER_MODE_STREAMING_GUARD)
    if configured_mode == KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING:
        return KbCitationRenderStrategy(
            mode=KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING,
            force_non_streaming=True,
        )
    return KbCitationRenderStrategy(mode=KB_RENDER_MODE_STREAMING_GUARD)
