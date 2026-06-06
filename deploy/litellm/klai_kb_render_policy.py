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
) -> KbCitationRenderStrategy:
    if original_stream is True:
        return KbCitationRenderStrategy(mode=KB_RENDER_MODE_STREAMING_GUARD)
    if configured_mode == KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING:
        return KbCitationRenderStrategy(
            mode=KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING,
            force_non_streaming=True,
        )
    return KbCitationRenderStrategy(mode=KB_RENDER_MODE_STREAMING_GUARD)
