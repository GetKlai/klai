"""Lightweight, privacy-gated tracing for the retrieval pipeline.

Add a step by extending ``StepName`` and wrapping the existing block with
``trace.step(...)`` (the same context manager supports ``with`` and
``async with``). Use ``meta`` for non-content values such as counts, booleans,
scores, and durations. Use ``content`` for raw or resolved query text; content
is rendered only at ``full`` telemetry. Never store payload dumps, chunk/source
text, credentials, or URLs in error metadata.

Skip reasons are intentionally bounded by ``SkippedReason``. Add a new reason
there before using it at a call site. Wrappers preserve exceptions by default;
``fail_open=True`` is an explicit per-step choice and is valid only where the
existing pipeline already fails open. Error messages are omitted unless the
caller supplies a known-safe ``safe_error_message``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal, Self, cast, get_args, overload

TraceStatus = Literal["ok", "skipped", "error"]
TelemetryLevel = Literal["off", "shadow", "full"]
StepName = Literal[
    "coreference",
    "embed",
    "gate",
    "router",
    "qdrant_search",
    "graph_search",
    "link_expand",
    "rerank",
    "quality_floor",
    "source_select",
    "quality_boost",
    "evidence_tier",
    "parent_lookup",
    "response_build",
    "confidence_band",
    "total",
]
SkippedReason = Literal[
    "gate_bypassed",
    "disabled_by_config",
    "no_candidates",
    "no_candidate_urls",
    "reranker_disabled",
    "shadow_mode",
    "no_verified_identity",
    "scope_not_applicable",
    "insufficient_source_labels",
]

STEP_NAMES = frozenset(get_args(StepName))
SKIPPED_REASONS = frozenset(get_args(SkippedReason))
_STEP_RESERVED_FIELDS = frozenset(
    {
        "name",
        "status",
        "duration_ms",
        "skipped_reason",
        "error_type",
        "error_message_safe",
    }
)


def _validated_step_name(name: StepName) -> StepName:
    if name not in STEP_NAMES:
        raise ValueError(f"Unknown retrieval trace step: {name}")
    return cast(StepName, name)


def _validated_skip_reason(reason: SkippedReason) -> SkippedReason:
    if reason not in SKIPPED_REASONS:
        raise ValueError(f"Unknown retrieval trace skip reason: {reason}")
    return cast(SkippedReason, reason)


@dataclass(slots=True)
class TraceStep:
    """One ordered trace entry plus its privacy-classified details."""

    name: StepName
    status: TraceStatus = "ok"
    duration_ms: float = 0.0
    skipped_reason: SkippedReason | None = None
    error_type: str | None = None
    error_message_safe: str | None = None
    _started_at: float | None = field(default=None, repr=False)
    _metadata: dict[str, Any] = field(default_factory=dict, repr=False)
    _content: dict[str, Any] = field(default_factory=dict, repr=False)

    def meta(self, key: str, value: Any) -> Self:
        """Attach a metadata-only detail and return this step."""
        if key in _STEP_RESERVED_FIELDS:
            raise ValueError(f"Reserved retrieval trace step field: {key}")
        if key in self._content:
            raise ValueError(f"Retrieval trace field already classified as content: {key}")
        self._metadata[key] = value
        return self

    def content(self, key: str, value: Any) -> Self:
        """Attach a content-bearing detail and return this step."""
        if key in _STEP_RESERVED_FIELDS:
            raise ValueError(f"Reserved retrieval trace step field: {key}")
        if key in self._metadata:
            raise ValueError(f"Retrieval trace field already classified as metadata: {key}")
        self._content[key] = value
        return self

    def skip(self, reason: SkippedReason, **metadata: Any) -> Self:
        """Mark this entered step skipped without changing control flow."""
        self.status = "skipped"
        self.skipped_reason = _validated_skip_reason(reason)
        for key, value in metadata.items():
            self.meta(key, value)
        return self

    def _finish(self, ended_at: float) -> None:
        if self._started_at is not None:
            self.duration_ms = (ended_at - self._started_at) * 1000

    def _record_error(
        self, exc: BaseException, ended_at: float, safe_error_message: str | None
    ) -> None:
        self._finish(ended_at)
        self.status = "error"
        self.skipped_reason = None
        self.error_type = type(exc).__name__
        self.error_message_safe = safe_error_message

    def _render(self, *, include_content: bool) -> dict[str, Any]:
        rendered: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 3),
            **self._metadata,
        }
        if self.skipped_reason is not None:
            rendered["skipped_reason"] = self.skipped_reason
        if self.error_type is not None:
            rendered["error_type"] = self.error_type
        if self.error_message_safe is not None:
            rendered["error_message_safe"] = self.error_message_safe
        if include_content:
            rendered.update(self._content)
        return rendered


class _StepContext:
    """Shared state for exception-preserving and fail-open wrappers."""

    def __init__(
        self,
        trace: RetrievalTrace,
        name: StepName,
        *,
        safe_error_message: str | None,
        started_at: float | None,
    ) -> None:
        self._trace = trace
        self._name: StepName = _validated_step_name(name)
        self._safe_error_message = safe_error_message
        self._started_at = started_at
        self._record: TraceStep | None = None

    def __enter__(self) -> TraceStep:
        started_at = self._started_at if self._started_at is not None else time.perf_counter()
        self._record = TraceStep(name=self._name, _started_at=started_at)
        self._trace._steps.append(self._record)
        return self._record

    async def __aenter__(self) -> TraceStep:
        return self.__enter__()

    def _complete(
        self,
        exc: BaseException | None,
    ) -> None:
        assert self._record is not None
        ended_at = time.perf_counter()
        if exc is None:
            self._record._finish(ended_at)
        else:
            self._record._record_error(exc, ended_at, self._safe_error_message)


class _ReraisingStepContext(_StepContext):
    """Context manager whose type guarantees exception propagation."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, traceback
        self._complete(exc)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, traceback)


class _FailOpenStepContext(_StepContext):
    """Context manager that suppresses only an exception from its step."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, traceback
        self._complete(exc)
        return exc is not None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return self.__exit__(exc_type, exc, traceback)


@dataclass(slots=True)
class RetrievalTrace:
    """Privacy-aware decision trace for one retrieval request."""

    request_id: str
    org_id: str
    scope: str
    telemetry_level: TelemetryLevel
    started_at: float
    _steps: list[TraceStep] = field(default_factory=list, init=False, repr=False)
    _metadata: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _content: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.telemetry_level not in get_args(TelemetryLevel):
            raise ValueError(f"Unknown telemetry level: {self.telemetry_level}")

    def meta(self, key: str, value: Any) -> Self:
        """Add a metadata-only top-level compatibility field."""
        if key in self._content:
            raise ValueError(f"Retrieval trace field already classified as content: {key}")
        self._metadata[key] = value
        return self

    def content(self, key: str, value: Any) -> Self:
        """Add a content-bearing top-level compatibility field."""
        if key in self._metadata:
            raise ValueError(f"Retrieval trace field already classified as metadata: {key}")
        self._content[key] = value
        return self

    @overload
    def step(
        self,
        name: StepName,
        *,
        fail_open: Literal[False] = False,
        safe_error_message: str | None = None,
        started_at: float | None = None,
    ) -> _ReraisingStepContext: ...

    @overload
    def step(
        self,
        name: StepName,
        *,
        fail_open: Literal[True],
        safe_error_message: str | None = None,
        started_at: float | None = None,
    ) -> _FailOpenStepContext: ...

    def step(
        self,
        name: StepName,
        *,
        fail_open: bool = False,
        safe_error_message: str | None = None,
        started_at: float | None = None,
    ) -> _StepContext:
        """Return a sync/async wrapper; exceptions re-raise unless opted out."""
        context_type = _FailOpenStepContext if fail_open else _ReraisingStepContext
        return context_type(
            self,
            name,
            safe_error_message=safe_error_message,
            started_at=started_at,
        )

    def mark_skipped(self, name: StepName, reason: SkippedReason, **metadata: Any) -> TraceStep:
        """Append a zero-duration skipped step."""
        record = TraceStep(
            name=_validated_step_name(name),
            status="skipped",
            skipped_reason=_validated_skip_reason(reason),
        )
        for key, value in metadata.items():
            record.meta(key, value)
        self._steps.append(record)
        return record

    def record_ok(self, name: StepName, duration_ms: float, **metadata: Any) -> TraceStep:
        """Append a successful step measured by existing pipeline timing."""
        record = TraceStep(name=_validated_step_name(name), duration_ms=duration_ms)
        for key, value in metadata.items():
            record.meta(key, value)
        self._steps.append(record)
        return record

    def record_error(
        self,
        name: StepName,
        exc: BaseException,
        duration_ms: float,
        safe_message: str | None = None,
        **metadata: Any,
    ) -> TraceStep:
        """Append safe error metadata for an existing fail-open boundary."""
        record = TraceStep(
            name=_validated_step_name(name),
            status="error",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message_safe=safe_message,
        )
        for key, value in metadata.items():
            record.meta(key, value)
        self._steps.append(record)
        return record

    def to_decision_record(self) -> dict[str, Any]:
        """Render flat compatibility fields plus ordered, privacy-safe steps."""
        include_content = self.telemetry_level == "full"
        rendered = dict(self._metadata)
        if include_content:
            rendered.update(self._content)
        rendered["trace_steps"] = [
            step._render(include_content=include_content) for step in self._steps
        ]
        has_rendered_content = include_content and bool(
            self._content or any(step._content for step in self._steps)
        )
        rendered["retention_class"] = "content" if has_rendered_content else "metadata"
        return rendered

    def to_log_kwargs(self) -> dict[str, Any]:
        """Render logger kwargs with request identity and privacy gating."""
        return {
            **self.to_decision_record(),
            "request_id": self.request_id,
            "org_id": self.org_id,
            "scope": self.scope,
            "telemetry_level": cast(str, self.telemetry_level),
        }
