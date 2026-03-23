# SPEC-MEETING-SUMMARY: Research Notes

**Created:** 2026-03-23

---

## 1. Current Architecture Analysis

### Vexa Integration (as-is)

The portal backend integrates with Vexa through two separate services:

**Bot-Manager API (port 8080):**
- `VexaClient` in `app/services/vexa.py` connects to `settings.vexa_bot_manager_url`
- Used for: `start_bot()`, `stop_bot()`, `get_meeting_by_native_id()`, `get_bot_status()`, `get_recording()`
- Authenticated via `X-API-Key` header
- This API does NOT expose transcript segments or speaker labels

**API-Gateway (port 8123) -- not yet integrated:**
- Endpoint: `GET /transcripts/{platform}/{native_meeting_id}`
- Returns: `{ segments: [{ start, end, text, speaker, language, absolute_start_time }] }`
- Speaker events (SPEAKER_START/END) are correctly detected by Vexa bots
- This is the source of speaker-labeled transcript data

### Current Transcription Pipeline (as-is)

The function `run_transcription()` in `app/api/meetings.py`:
1. Downloads raw audio from Vexa via `vexa.get_recording(vexa_meeting_id)` (polls up to 5 times)
2. Sends audio to Whisper server (`settings.whisper_server_url/v1/audio/transcriptions`)
3. Stores `transcript_text` from Whisper output (flat text, no speaker labels)
4. Sets `transcript_segments = None` (hardcoded!)
5. Stores `language` and `duration_seconds` from Whisper response

**Key finding:** Line 325 in `meetings.py` explicitly sets `meeting.transcript_segments = None`, confirming that the current pipeline never populates this field even though the JSONB column exists.

### Database Schema (as-is)

`VexaMeeting` model in `app/models/meetings.py`:
- `transcript_text: Text, nullable` -- populated by Whisper (flat text)
- `transcript_segments: JSONB, nullable` -- exists but always NULL
- `language: String(16), nullable` -- populated by Whisper
- No `summary_text` column yet -- requires migration

### Frontend (as-is)

`$meetingId.tsx` already has:
- `TranscriptSegment` interface with `{ start, end, text, speaker }` fields
- Conditional rendering: if `transcript_segments` exists, renders speaker-labeled view; otherwise renders flat `transcript_text`
- Copy and download functionality for transcript
- No summary display or summarize button

### Config (as-is)

`Settings` in `app/core/config.py`:
- `vexa_bot_manager_url` -- for port 8080
- `vexa_api_key` -- shared API key
- `vexa_webhook_secret` -- webhook auth
- `whisper_server_url` -- Whisper endpoint
- `litellm_master_key` -- LiteLLM key (already exists!)
- No `vexa_api_gateway_url` (port 8123) yet
- No `litellm_base_url` yet
- No `summarize_model` yet

---

## 2. Vexa API-Gateway Transcript Format

Based on the provided information, the API-gateway response structure:

```json
{
  "segments": [
    {
      "start": 0.0,
      "end": 3.5,
      "text": "Good morning everyone, let's get started.",
      "speaker": "Alice van der Berg",
      "language": "en",
      "absolute_start_time": "2026-03-23T10:00:00.000Z"
    },
    {
      "start": 4.0,
      "end": 7.2,
      "text": "Thanks Alice. I have a quick update on the migration.",
      "speaker": "Bob Jansen",
      "language": "en",
      "absolute_start_time": "2026-03-23T10:00:04.000Z"
    }
  ]
}
```

Key observations:
- `speaker` field is a participant name (string), not a numeric ID
- `language` is per-segment (may vary in multilingual meetings)
- `absolute_start_time` is UTC ISO format
- `start` and `end` are relative to meeting start (seconds, float)

---

## 3. Noise Artifacts Analysis

### Known patterns from Google Meet

1. **Subtitle watermarks:** "Sous-titrage ST' 501" -- French subtitle attribution text injected by Google Meet's live captioning feature. Appears as segments with no speaker, very short duration, and this specific pattern.

2. **Identified regex patterns:**
   - `Sous-titrage` (literal match)
   - `ST'\s*\d+` (subtitle timestamp markers)
   - Subtitle watermarks from other platforms (to be identified during implementation)

3. **Speakerless short segments:** Segments with `speaker: null` and duration < 2 seconds are typically platform artifacts, not actual speech.

4. **Consecutive duplicates:** Same text repeated within 5 seconds indicates platform echo or duplicate event. Keep the first occurrence.

5. **Punctuation-only segments:** Segments containing only `.`, `...`, `,`, or whitespace are transcription artifacts.

### Filtering strategy

Apply filters in this order (most aggressive first):
1. Empty/whitespace/punctuation-only text (cheapest check)
2. Subtitle watermark regex patterns
3. Short speakerless segments (duration < 2s, no speaker)
4. Consecutive duplicates (same text within 5s)

This order ensures earlier filters reduce the dataset for more expensive checks.

---

## 4. Summarization Approach Research

### Industry practices (Otter.ai, Fireflies, Grain, Microsoft Copilot)

All major meeting summarization tools use multi-prompt architectures rather than a single "summarize this" prompt:

1. **Otter.ai:** Extraction + synthesis + action item extraction (3 prompts)
2. **Fireflies.ai:** Key topics + action items + outline (3 separate outputs)
3. **Grain:** Highlights extraction + summary generation (2 prompts)
4. **Microsoft Copilot:** Structured extraction + narrative synthesis (2 prompts)

### Two-prompt approach (selected)

**Prompt 1: Extraction**
- Input: cleaned transcript (with speaker labels when available)
- Output: structured JSON with `speakers_present`, `topics`, `decisions`, `action_items`, `open_questions`, `next_steps`
- Chain-of-thought instruction: "First identify speakers, then extract topics chronologically, then identify decisions..."
- For unlabeled transcripts: omit speaker attribution, focus on content

**Prompt 2: Synthesis**
- Input: extracted facts JSON + target language
- Output: readable meeting summary in the transcript's language
- Structure: executive summary paragraph, then bulleted sections for decisions, action items, open questions
- Language instruction: "Write the summary in {language}. Do not translate proper nouns."

### Why two prompts, not one?

- **Separation of concerns:** Extraction is factual (less creative), synthesis is editorial (more creative). Different temperature settings.
- **Debuggability:** Can inspect extracted facts to verify correctness before synthesis.
- **Reusability:** Extracted facts could power other features (action item tracking, search indexing).
- **Quality:** Research shows chain-of-thought outperforms single "summarize" prompt for structured output.

### Single-speaker / no-label handling

When `transcript_segments` is NULL or all segments lack speaker labels:
- Extraction prompt omits "identify speakers" step
- Output omits `speakers_present` or lists "Unknown Speaker"
- No false attribution: never invent speaker names
- Focus on: topics, decisions, action items, timeline

### Model selection

- Default: `gpt-4o-mini` via LiteLLM (cost-effective, fast, sufficient quality)
- Configurable via `settings.summarize_model` for future upgrades
- Temperature: 0.1 for extraction (factual), 0.3 for synthesis (slightly creative)

### Language handling

- Detect language from `vexa_meetings.language` field
- Synthesis prompt explicitly instructs: "Write the summary in {language_name}"
- Language mapping: `nl` -> "Dutch", `en` -> "English", `de` -> "German", `fr` -> "French"
- Fallback: if language is null, instruct "Write the summary in the same language as the transcript"

---

## 5. LiteLLM Integration

LiteLLM is already deployed in the Klai stack on core-01. The portal backend has `settings.litellm_master_key` available.

**Integration approach:**
- Use `httpx.AsyncClient` to call LiteLLM's OpenAI-compatible API directly
- Endpoint: `{litellm_base_url}/v1/chat/completions`
- Auth: `Authorization: Bearer {litellm_master_key}`
- No need for the `litellm` Python SDK -- direct HTTP is simpler and avoids a dependency

**Why not use the litellm Python SDK:**
- The portal backend is a lean FastAPI app; adding the full `litellm` package is heavy
- The OpenAI-compatible API is well-defined and stable
- Direct HTTP via `httpx` is already the pattern used throughout the codebase (see `vexa.py`)

---

## 6. Migration Strategy

### Database migration

```sql
ALTER TABLE vexa_meetings ADD COLUMN summary_text TEXT;
```

Simple column addition, no data migration needed. The column is nullable with no default.

### Deployment order

1. Deploy migration first (add column -- backward compatible)
2. Deploy backend with new endpoint and updated `run_transcription()`
3. Deploy frontend with summary UI

This ordering ensures no downtime: the new column exists before code references it, and the backend handles the new column before the frontend calls the endpoint.

---

## 7. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Vexa API-gateway is down/unreachable | Medium | Medium | Fallback to Whisper pipeline (REQ-TS-004) |
| LiteLLM rate limiting | Low | Low | Return 502, user can retry; no data corruption |
| Transcript too long for LLM context | Low | Medium | A-7 assumes < 100K tokens; add truncation if exceeded |
| Noise patterns not comprehensive | Medium | Low | Filter is additive; new patterns can be added later |
| Incorrect speaker attribution from Vexa | Low | Medium | We display what Vexa provides; no post-processing |
| Summary quality varies by language | Medium | Low | Use strong multilingual model; configurable model name |

---

## 8. Resolved Questions

1. **API-gateway authentication:** ✅ CONFIRMED — port 8123 uses the same `X-API-Key` header and value (`VEXA_BOT_MANAGER_API_KEY`) as the bot-manager at port 8080. Verified by live API call. No new secret needed.

2. **Summary storage format:** ✅ DECIDED — single `summary_json JSONB` column containing `{ "markdown": "...", "structured": { ... } }`. Industry standard: structured JSON as canonical store, Markdown derived for display. Both in one column ensures they stay in sync.

3. **Transcript storage:** ✅ DECIDED — `transcript_segments` JSONB is the canonical source (preserves timestamps, speakers, confidence). `transcript_text` is a derived cache rebuilt from segments in `[Speaker]: text` format. Never store only flat text when segments are available.

## 9. Open Questions

All open questions resolved. Summarization is a manual user action (button click) — the user only sees the button when the meeting is already `done` with a transcript present. This eliminates:

1. **Webhook timing** — no race condition: by the time the user clicks, segments are long available.
2. **Rate limiting** — one call per user click, no batch processing.
3. **Historical backfill** — the button works on any `done` meeting with a transcript, old or new. No separate backfill needed.
