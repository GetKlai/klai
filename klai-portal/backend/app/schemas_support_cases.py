"""Validated support-case payload contract (SPEC-RAG-SUPPORT-GAP).

The evidence store, the connector-facing internal ingest endpoint and the
transcript importer share one Pydantic model so a case has exactly one shape on
disk (``docs/architecture/support-gap-detection.md`` → "Case payload and
storage").

Untrusted-input rule: message text is evidence, never instructions. Nothing
here executes or interpolates it — it is stored verbatim in JSONB and rendered
back through the API with the same validation.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

MessageKind = Literal["message", "note", "email", "transcript", "ticket"]
MessageRole = Literal["customer", "agent", "unknown"]
MessageVisibility = Literal["customer", "internal", "unknown"]
MessageMedium = Literal["call", "email", "chat", "unknown"]

# A legacy payload predates the medium field; derive it from the kind so an
# omitted medium hashes and analyses the same as one written today. A transcript
# is a call and an email is email; every other kind stays unknown (a ticket is a
# case wrapper, a note an internal aside, a conversation message needs its
# channel to know the medium — none of which the kind alone establishes).
_KIND_MEDIUM: dict[MessageKind, MessageMedium] = {"transcript": "call", "email": "email"}
CaseSource = Literal["hubspot", "audio"]

# State machine for ``portal_support_cases.status``.
CaseStatus = Literal["incomplete", "pending", "analyzed", "failed"]


class SupportCaseMessage(BaseModel):
    """One ordered piece of evidence inside a case.

    ``occurred_at`` is a source timestamp, never derived from a filename or a
    processing time; it stays ``None`` when the source does not supply a real
    one. ``start_seconds`` / ``end_seconds`` carry transcript segment timing.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: MessageKind
    role: MessageRole
    text: str
    occurred_at: str | None = None
    visibility: MessageVisibility = "unknown"
    # Source exchange structure. ``thread_id`` groups the source exchange;
    # ``reply_to_id`` is a provider-supplied canonical message reference, never
    # a chronological guess. ``speaker_id`` is a diarization/actor label, kept
    # separate from the customer/agent role.
    medium: MessageMedium = "unknown"
    channel_id: str | None = None
    thread_id: str | None = None
    reply_to_id: str | None = None
    speaker_id: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None

    @model_validator(mode="after")
    def _infer_medium_from_legacy_kind(self) -> SupportCaseMessage:
        if self.medium == "unknown":
            self.medium = _KIND_MEDIUM.get(self.kind, "unknown")
        return self

    @model_validator(mode="after")
    def _validate_segment_timing(self) -> SupportCaseMessage:
        # Malformed transcript timing is rejected rather than silently stored: a
        # non-finite, negative or reversed segment would draw a nonsense span in
        # the evidence view. Absent timing (both None) is fine — text-only
        # sources. Cross-segment ordering and duration bounds are checked by the
        # transcript normalizer, which has the whole recording in view.
        start, end = self.start_seconds, self.end_seconds
        if start is not None and (not math.isfinite(start) or start < 0):
            raise ValueError(f"start_seconds must be a finite value >= 0, got {start}")
        if end is not None and (not math.isfinite(end) or end < 0):
            raise ValueError(f"end_seconds must be a finite value >= 0, got {end}")
        if start is not None and end is not None and end < start:
            raise ValueError(f"end_seconds ({end}) precedes start_seconds ({start})")
        return self


class SupportCasePayload(BaseModel):
    """The full validated case body the store persists in JSONB.

    Identity is ``(org_id, kb_slug, source, account_id, external_id)``. The
    server derives ``org_id`` and ``kb_slug`` from the active connector, so a
    caller cannot choose another tenant here — ``kb_slug`` is bound by the store
    via :meth:`bind_kb`, not read from the client body. ``external_id`` is the
    source's stable id (a HubSpot ticket id, or a recording sha256), never a
    filename.
    """

    model_config = ConfigDict(extra="forbid")

    source: CaseSource
    account_id: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    subject: str = ""
    language: str | None = None
    source_url: str | None = None
    source_updated_at: str | None = None
    complete: bool = True
    incomplete_reasons: list[str] = Field(default_factory=list)
    messages: list[SupportCaseMessage] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    # Server-bound comparison-scope KB slug; part of the stable identity but
    # never client-supplied.
    _kb_slug: str = PrivateAttr(default="")

    def bind_kb(self, kb_slug: str) -> None:
        self._kb_slug = kb_slug

    def kb_key(self) -> str:
        """Identity-minus-org string used for the advisory serialization lock."""
        return "|".join([self._kb_slug, self.source, self.account_id, self.external_id])

    def content_hash(self) -> str:
        """Deterministic hash of the evidence, for change detection.

        Covers the parts a repeat sync must react to — language, completeness
        and every message — and excludes provenance that shifts without the
        evidence changing (``source_url``, ``source_updated_at``, ``metadata``).
        For ``audio`` the subject is the recording filename, so it is excluded
        too: renaming a copy of the same recording must not rerun analysis or
        reopen closed findings. For ``hubspot`` the subject is the ticket
        subject and IS meaningful, so it stays in the hash.
        """
        material = {
            "source": self.source,
            "account_id": self.account_id,
            "external_id": self.external_id,
            # Audio subject == filename (provenance, not evidence).
            "subject": "" if self.source == "audio" else self.subject,
            "language": self.language,
            "complete": self.complete,
            "incomplete_reasons": sorted(self.incomplete_reasons),
            "messages": [
                {
                    "id": m.id,
                    "kind": m.kind,
                    "role": m.role,
                    "text": m.text,
                    "occurred_at": m.occurred_at,
                    "visibility": m.visibility,
                    "medium": m.medium,
                    "channel_id": m.channel_id,
                    "thread_id": m.thread_id,
                    "reply_to_id": m.reply_to_id,
                    "speaker_id": m.speaker_id,
                    "start_seconds": m.start_seconds,
                    "end_seconds": m.end_seconds,
                }
                for m in self.messages
            ],
        }
        canonical = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def initial_status(self) -> CaseStatus:
        """Status before analysis: ``incomplete`` when the source read was
        partial, otherwise ``pending`` (complete evidence awaiting analysis)."""
        return "pending" if self.complete else "incomplete"
