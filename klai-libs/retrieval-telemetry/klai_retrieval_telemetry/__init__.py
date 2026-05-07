"""klai-retrieval-telemetry — shared retrieval telemetry helpers.

Public surface:
    classify_gap(chunks) -> Optional[str]
    fire_retrieval_log(...) -> None  (fire-and-forget)
    fire_gap_event(...) -> None      (fire-and-forget)
    RetrievalTelemetryConfig         (override default env-driven config)

Behaviour preservation: the three helpers are byte-identical in observable
behaviour to the inline implementations in deploy/litellm/klai_knowledge.py
prior to SPEC-MCP-RETRIEVAL-001 Phase 1 extraction. The optional
``caller_client_id`` keyword is the only contract addition.
"""

from klai_retrieval_telemetry._emit import (
    RetrievalTelemetryConfig,
    classify_gap,
    fire_gap_event,
    fire_retrieval_log,
)

__all__ = [
    "RetrievalTelemetryConfig",
    "classify_gap",
    "fire_gap_event",
    "fire_retrieval_log",
]
