# SPEC-MEETING-SUMMARY: Meeting Transcript Pipeline & AI Summarization

**SPEC ID:** SPEC-MEETING-SUMMARY
**Status:** Planned
**Priority:** High
**Created:** 2026-03-23

---

## Environment

- **Platform:** Klai Portal (SaaS meeting transcription)
- **Backend:** FastAPI at `klai-mono/portal/backend/`
- **Frontend:** TanStack Router + React at `klai-mono/portal/frontend/`
- **Database:** PostgreSQL with SQLAlchemy 2.0 async, Alembic migrations
- **LLM gateway:** LiteLLM (already deployed on core-01, master key in `settings.litellm_master_key`)
- **Transcription source:** Vexa bot-manager API (port 8080) for bot lifecycle; Vexa API-gateway (port 8123) for transcript segments
- **i18n:** Paraglide (`messages/nl.json`, `messages/en.json`)
- **Auth:** Zitadel OIDC via `_get_user_id` dependency

## Assumptions

- A-1: Vexa API-gateway at port 8123 exposes `GET /transcripts/{platform}/{native_meeting_id}` returning `{ segments: [{ start, end, text, speaker, language, absolute_start_time }] }`.
- A-2: The `vexa_meetings.transcript_segments` JSONB column already exists in the schema but is always NULL because the current `run_transcription` flow uses Whisper audio transcription and never calls the segments endpoint.
- A-3: Speaker events (SPEAKER_START/END) are correctly detected by Vexa bots, so the `speaker` field in segments is populated for most segments.
- A-4: LiteLLM is reachable at `settings.litellm_base_url` (or a new config field) with the existing master key and exposes an OpenAI-compatible `/v1/chat/completions` endpoint.
- A-5: Noise artifacts like "Sous-titrage ST' 501" appear as segments with no speaker, very short duration, or repeated identical text. These are Google Meet subtitle watermarks injected by the platform.
- A-6: The `summary_json` column does not yet exist on `vexa_meetings` and requires a new Alembic migration.
- A-7: A single meeting transcript typically fits within the context window of the target LLM (< 100K tokens). Chunking/map-reduce is not required for the initial implementation.
- A-8: **Confirmed:** The Vexa API-gateway (port 8123) uses the same `X-API-Key` header and value as the bot-manager (port 8080). No additional authentication config is needed.
- A-9: `transcript_segments` JSONB is the canonical source of truth for transcripts. `transcript_text` is a derived cache, rebuilt from segments. Industry standard: store structured segments, derive flat text from them.

---

## Requirements

### Part 1: Transcript Segment Pipeline

**REQ-TS-001 (Event-driven):**
WHEN the Vexa webhook reports status `completed` for a meeting, THEN the portal backend shall call Vexa API-gateway `GET /transcripts/{platform}/{native_meeting_id}` to fetch transcript segments with speaker labels.

**REQ-TS-002 (Event-driven):**
WHEN transcript segments are successfully fetched from the API-gateway, THEN the system shall store the cleaned segment array in `vexa_meetings.transcript_segments` as JSONB.

**REQ-TS-003 (Event-driven):**
WHEN transcript segments are stored, THEN the system shall rebuild `vexa_meetings.transcript_text` from the cleaned segments in the format `[speaker]: text` (one line per segment), replacing any Whisper-only flat transcript.

**REQ-TS-004 (State-driven):**
IF the API-gateway call fails or returns empty segments, THEN the system shall fall back to the existing Whisper audio transcription pipeline, leaving `transcript_segments` as NULL and `transcript_text` populated from Whisper output.

**REQ-TS-005 (Ubiquitous):**
The system shall always store the detected `language` field from the first non-empty segment (or from Whisper fallback) in `vexa_meetings.language`.

### Part 2: Noise Filtering

**REQ-NF-001 (Ubiquitous):**
The system shall always filter transcript segments before storage by removing segments that match any of the following noise patterns.

**REQ-NF-002 (Event-driven):**
WHEN a segment's text matches known subtitle artifact patterns (regex: `Sous-titrage`, `ST'\s*\d+`, or other known subtitle watermarks), THEN the system shall discard that segment.

**REQ-NF-003 (Event-driven):**
WHEN a segment has no speaker label AND a duration less than 2 seconds (`end - start < 2.0`), THEN the system shall discard that segment.

**REQ-NF-004 (Event-driven):**
WHEN a segment's text is identical to the immediately preceding segment's text AND the time gap between them is less than 5 seconds, THEN the system shall discard the duplicate segment.

**REQ-NF-005 (Event-driven):**
WHEN a segment's text consists solely of punctuation, whitespace, or is empty after stripping, THEN the system shall discard that segment.

### Part 3: AI Summarization

**REQ-SUM-001 (Event-driven):**
WHEN a user calls `POST /api/bots/meetings/{id}/summarize`, THEN the system shall generate an AI summary using a two-prompt approach via LiteLLM.

**REQ-SUM-002 (State-driven):**
IF `transcript_segments` is available with speaker labels, THEN the extraction prompt shall use the speaker-labeled transcript format. IF only `transcript_text` is available (no segments), THEN the extraction prompt shall use the flat text and shall NOT attempt false speaker attribution.

**REQ-SUM-003 (Event-driven):**
WHEN the extraction prompt is executed, THEN it shall produce structured JSON containing: speakers present, topics discussed, decisions made, action items (with owner if identifiable), open questions, and next steps.

**REQ-SUM-004 (Event-driven):**
WHEN the synthesis prompt is executed with the extracted facts, THEN it shall produce a readable meeting summary in the same language as the transcript (detected from `vexa_meetings.language`).

**REQ-SUM-005 (Event-driven):**
WHEN summarization completes successfully, THEN the system shall store the result in `vexa_meetings.summary_json` (JSONB column, new migration required) with the structure:
```json
{
  "markdown": "<readable summary in Markdown>",
  "structured": {
    "speakers": ["Name"],
    "topics": ["..."],
    "decisions": ["..."],
    "action_items": [{"owner": "Name or null", "task": "..."}],
    "open_questions": ["..."],
    "next_steps": ["..."]
  }
}
```
The `markdown` field is for human display; `structured` is for future machine processing.

**REQ-SUM-006 (Unwanted):**
The system shall NOT allow summarization of a meeting that has no transcript (`transcript_text` is NULL or empty). The endpoint shall return HTTP 422 with a clear error message.

**REQ-SUM-007 (Unwanted):**
The system shall NOT re-summarize a meeting that already has a `summary_json` without explicit user confirmation (query parameter `force=true`).

**REQ-SUM-008 (State-driven):**
IF the LiteLLM call fails (timeout, rate limit, model error), THEN the endpoint shall return HTTP 502 with a descriptive error and shall NOT store a partial summary.

### Part 4: Frontend / UI

**REQ-UI-001 (State-driven):**
IF a meeting has status `done` and has a transcript, THEN the meeting detail page shall display a "Summarize" button.

**REQ-UI-002 (State-driven):**
IF a meeting already has a `summary_json`, THEN the meeting detail page shall display the `markdown` field rendered as Markdown in a card below the transcript, and the "Summarize" button shall change to "Re-summarize".

**REQ-UI-003 (Event-driven):**
WHEN the user clicks "Summarize", THEN the UI shall show a loading spinner on the button (disabled state) and display the summary card once the API responds.

**REQ-UI-004 (Event-driven):**
WHEN the summarization API returns an error, THEN the UI shall display a toast notification with the error message.

**REQ-UI-005 (Ubiquitous):**
The system shall always provide i18n translations for all new UI strings in both `nl.json` and `en.json`.

---

## Specifications

### Backend Changes

**S-1: New Vexa API-gateway client method**
Add `get_transcript_segments(platform, native_meeting_id)` to `VexaClient` in `app/services/vexa.py`. This method calls `GET /transcripts/{platform}/{native_meeting_id}` on the API-gateway (port 8123). Uses the same `X-API-Key` and `vexa_api_key` as the bot-manager — confirmed working. A new `vexa_api_gateway_url` setting points to port 8123. Returns `list[dict]` or raises on failure.

**S-2: Noise filter module**
Create `app/services/transcript_filter.py` with a `filter_segments(segments: list[dict]) -> list[dict]` function implementing REQ-NF-001 through REQ-NF-005. Pure function, fully unit-testable.

**S-3: Updated transcription flow**
Modify `run_transcription()` in `app/api/meetings.py`:
1. First, attempt to fetch segments from API-gateway via `vexa.get_transcript_segments()`.
2. If successful, run `filter_segments()`, store in `transcript_segments`, rebuild `transcript_text`.
3. If API-gateway fails, fall back to existing Whisper audio pipeline.

**S-4: Summarization service**
Create `app/services/summarizer.py` with:
- `extract_facts(transcript: str, segments: list[dict] | None, language: str) -> dict` — calls LiteLLM extraction prompt, returns structured dict
- `synthesize_summary(facts: dict, language: str) -> str` — calls LiteLLM synthesis prompt, returns Markdown string
- `summarize_meeting(meeting: VexaMeeting) -> dict` — orchestrates both prompts, returns `{ "markdown": str, "structured": dict }`

**S-5: Summarization endpoint**
Add `POST /api/bots/meetings/{meeting_id}/summarize` with query param `force: bool = False`. Auth via `_get_user_id`. Returns `{ summary: { markdown: str, structured: dict } }`.

**S-6: DB migration**
Alembic migration adding `summary_json JSONB` column to `vexa_meetings`. Structure:
```json
{ "markdown": "...", "structured": { "speakers": [], "topics": [], "decisions": [], "action_items": [], "open_questions": [], "next_steps": [] } }
```

**S-7: Config additions**
Add to `Settings` in `app/core/config.py`:
- `vexa_api_gateway_url: str = ""` (base URL for port 8123, same API key as bot-manager)
- `litellm_base_url: str = ""` (if not already present)
- `summarize_model: str = "gpt-4o-mini"` (configurable model name via LiteLLM)

### Frontend Changes

**S-8: Summary UI**
In `$meetingId.tsx`:
- Add summary card below transcript card
- Add "Summarize" / "Re-summarize" button
- Add loading state during API call
- Add error toast on failure

**S-9: API response model update**
Add `summary_json: dict | None` to `MeetingResponse` (backend Pydantic model) and `summary_json: { markdown: string; structured: SummaryStructured } | null` to the `MeetingDetail` TypeScript interface in the frontend.

**S-10: i18n keys**
Add keys to `nl.json` and `en.json`:
- `app_meetings_summarize_button`
- `app_meetings_resummarize_button`
- `app_meetings_summary_title`
- `app_meetings_summary_loading`
- `app_meetings_summary_error`

### Files Affected

| File | Change |
|------|--------|
| `portal/backend/app/services/vexa.py` | Add API-gateway client, `get_transcript_segments()` |
| `portal/backend/app/services/transcript_filter.py` | New: noise filtering logic |
| `portal/backend/app/services/summarizer.py` | New: two-prompt summarization service |
| `portal/backend/app/api/meetings.py` | Update `run_transcription()`, add summarize endpoint, update response model |
| `portal/backend/app/models/meetings.py` | Add `summary_json` JSONB column |
| `portal/backend/app/core/config.py` | Add new settings fields |
| `portal/backend/alembic/versions/xxx_add_summary_json.py` | New migration: add `summary_json JSONB` |
| `portal/frontend/src/routes/app/meetings/$meetingId.tsx` | Summary UI, button, loading state |
| `portal/frontend/messages/nl.json` | New i18n keys |
| `portal/frontend/messages/en.json` | New i18n keys |
