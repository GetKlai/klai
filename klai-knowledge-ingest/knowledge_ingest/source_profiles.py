"""Source knowledge profiles for assertion-mode defaults.

The profile is deliberately resolved at the ingest boundary so every caller
gets the same taxonomy contract, while adapters can still narrow the allowed
assertion modes when they know more about a source.
"""

from __future__ import annotations

from dataclasses import dataclass

from knowledge_ingest.models import VALID_ASSERTION_MODES

DEFAULT_ASSERTION_MODES: tuple[str, ...] = (
    "factual",
    "belief",
    "hypothesis",
    "procedural",
    "quoted",
    "unknown",
)


@dataclass(frozen=True)
class SourceKnowledgeProfile:
    profile_name: str
    content_type: str
    allowed_assertion_modes: tuple[str, ...]
    default_provenance_type: str = "observed"
    default_confidence: str | None = None
    default_synthesis_depth: int = 0


def _valid_modes(modes: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not modes:
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for mode in modes:
        if mode in VALID_ASSERTION_MODES and mode not in seen:
            result.append(mode)
            seen.add(mode)
    return tuple(result)


def _profile_name(source_type: str | None, content_type: str, connector_type: str | None) -> str:
    source_part = source_type or "unknown"
    if connector_type:
        source_part = f"{source_part}:{connector_type}"
    return f"{source_part}:{content_type or 'unknown'}"


def resolve_source_knowledge_profile(
    *,
    source_type: str | None,
    content_type: str,
    connector_type: str | None = None,
    allowed_assertion_modes: list[str] | None = None,
    synthesis_depth: int | None = None,
) -> SourceKnowledgeProfile:
    """Resolve the canonical knowledge profile for one ingest request.

    Caller-provided assertion-mode hints win when at least one valid mode is
    supplied. Otherwise the content/source profile falls back to the full
    six-value taxonomy, which keeps classification possible without assigning
    an unwarranted default mode.
    """
    normalized_content_type = content_type or "unknown"
    modes = _valid_modes(allowed_assertion_modes) or DEFAULT_ASSERTION_MODES
    default_synthesis_depth = 4 if source_type == "docs" else 0
    if synthesis_depth is not None:
        default_synthesis_depth = synthesis_depth

    return SourceKnowledgeProfile(
        profile_name=_profile_name(source_type, normalized_content_type, connector_type),
        content_type=normalized_content_type,
        allowed_assertion_modes=modes,
        default_synthesis_depth=default_synthesis_depth,
    )
