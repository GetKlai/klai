# SPEC-MEETING-SUMMARY: Acceptance Criteria

**SPEC ID:** SPEC-MEETING-SUMMARY
**Created:** 2026-03-23

---

## Part 1: Transcript Segment Pipeline

### AC-TS-001: Segments fetched on meeting completion

```gherkin
Given a meeting with status "recording" and a valid platform/native_meeting_id
When the Vexa webhook fires with status "completed"
Then the backend calls GET /transcripts/{platform}/{native_meeting_id} on the API-gateway (port 8123)
And stores the filtered segments in vexa_meetings.transcript_segments as JSONB
And rebuilds transcript_text from segments in "[speaker]: text" format
And sets the meeting status to "done"
```

### AC-TS-002: Language detection from segments

```gherkin
Given transcript segments are fetched successfully
And the first non-empty segment has language "nl"
When the segments are stored
Then vexa_meetings.language is set to "nl"
```

### AC-TS-003: Fallback to Whisper on API-gateway failure

```gherkin
Given a meeting with status "completed"
When the API-gateway call to /transcripts/{platform}/{native_meeting_id} fails with a network error or HTTP 500
Then the system falls back to the Whisper audio transcription pipeline
And transcript_text is populated from Whisper output
And transcript_segments remains NULL
And meeting status is still set to "done"
```

### AC-TS-004: Fallback to Whisper on empty segments

```gherkin
Given a meeting with status "completed"
When the API-gateway returns an empty segments array
Then the system falls back to the Whisper audio transcription pipeline
And transcript_segments remains NULL
```

### AC-TS-005: Rebuilt transcript_text format

```gherkin
Given segments: [
  { "speaker": "Alice", "text": "Hello everyone", "start": 0.0, "end": 2.5 },
  { "speaker": "Bob", "text": "Hi Alice", "start": 3.0, "end": 4.0 }
]
When the transcript_text is rebuilt from segments
Then the result is:
  "Alice: Hello everyone\nBob: Hi Alice"
```

---

## Part 2: Noise Filtering

### AC-NF-001: Subtitle watermark removal

```gherkin
Given a segment with text "Sous-titrage ST' 501" and no speaker
When the noise filter runs
Then this segment is discarded
```

### AC-NF-002: Subtitle pattern variations

```gherkin
Given segments with texts:
  - "Sous-titrage ST' 501"
  - "ST' 234"
  - "Sous-titrage MFP"
When the noise filter runs
Then all three segments are discarded
```

### AC-NF-003: Short speakerless segment removal

```gherkin
Given a segment with speaker=null, start=10.0, end=11.5 (duration 1.5s)
When the noise filter runs
Then this segment is discarded
```

### AC-NF-004: Short segment WITH speaker is kept

```gherkin
Given a segment with speaker="Alice", start=10.0, end=11.5 (duration 1.5s), text="Yes"
When the noise filter runs
Then this segment is kept
```

### AC-NF-005: Consecutive duplicate removal

```gherkin
Given two consecutive segments:
  - { text: "Thank you", start: 10.0, end: 11.0, speaker: "Alice" }
  - { text: "Thank you", start: 12.0, end: 13.0, speaker: "Alice" }
And the gap between them is 1.0 seconds (< 5 seconds)
When the noise filter runs
Then the second (duplicate) segment is discarded
And the first segment is kept
```

### AC-NF-006: Non-consecutive duplicates are kept

```gherkin
Given two segments with identical text but 10 seconds apart
When the noise filter runs
Then both segments are kept
```

### AC-NF-007: Whitespace/punctuation-only removal

```gherkin
Given segments with texts: "   ", "...", "", "  .  "
When the noise filter runs
Then all four segments are discarded
```

### AC-NF-008: Valid segments pass through

```gherkin
Given a segment with speaker="Alice", text="Let's discuss the roadmap", duration=4.2s
When the noise filter runs
Then this segment passes through unchanged
```

---

## Part 3: AI Summarization

### AC-SUM-001: Successful summarization with speaker-labeled transcript

```gherkin
Given a meeting with status "done"
And transcript_segments containing segments with speaker labels
And transcript_text is populated
When the user calls POST /api/bots/meetings/{id}/summarize
Then the system sends an extraction prompt to LiteLLM containing the speaker-labeled transcript
And sends a synthesis prompt with the extracted facts
And stores the result in vexa_meetings.summary_json
And returns { "summary": { "markdown": "...", "structured": { ... } } } with HTTP 200
```

### AC-SUM-002: Summarization with flat text (no segments)

```gherkin
Given a meeting with status "done"
And transcript_segments is NULL
And transcript_text contains flat text from Whisper
When the user calls POST /api/bots/meetings/{id}/summarize
Then the extraction prompt uses flat text without speaker attribution
And the summary does NOT contain false speaker names
```

### AC-SUM-003: Summary language matches transcript

```gherkin
Given a meeting with language "nl"
When summarization is executed
Then the synthesis prompt instructs the LLM to write the summary in Dutch
And the resulting summary_json is in Dutch
```

### AC-SUM-004: No transcript available

```gherkin
Given a meeting with status "done" but transcript_text is NULL
When the user calls POST /api/bots/meetings/{id}/summarize
Then the API returns HTTP 422 with detail "No transcript available for summarization"
```

### AC-SUM-005: Meeting not found or unauthorized

```gherkin
Given a meeting that does not exist or belongs to a different user
When the user calls POST /api/bots/meetings/{id}/summarize
Then the API returns HTTP 404
```

### AC-SUM-006: No re-summarization without force flag

```gherkin
Given a meeting that already has summary_json populated
When the user calls POST /api/bots/meetings/{id}/summarize without force=true
Then the API returns HTTP 409 with detail "Summary already exists. Use force=true to re-summarize."
```

### AC-SUM-007: Force re-summarization

```gherkin
Given a meeting that already has summary_json populated
When the user calls POST /api/bots/meetings/{id}/summarize?force=true
Then the system generates a new summary and overwrites summary_json
And returns HTTP 200 with the new summary
```

### AC-SUM-008: LLM failure handling

```gherkin
Given a meeting with a valid transcript
When the user calls POST /api/bots/meetings/{id}/summarize
And the LiteLLM call fails (timeout, rate limit, or model error)
Then the API returns HTTP 502 with a descriptive error message
And summary_json remains NULL (no partial data stored)
```

### AC-SUM-009: Extraction prompt output structure

```gherkin
Given a transcript with multiple speakers discussing a project
When the extraction prompt is executed
Then the JSON output contains:
  - speakers_present: list of speaker names
  - topics: list of discussed topics
  - decisions: list of decisions made
  - action_items: list of { description, owner (if identifiable) }
  - open_questions: list of unresolved questions
  - next_steps: list of planned next steps
```

---

## Part 4: Frontend / UI

### AC-UI-001: Summarize button visibility

```gherkin
Given a meeting with status "done" and transcript_text is not empty
And summary_json is NULL
When the user views the meeting detail page
Then a "Summarize" button is visible below the transcript card
```

### AC-UI-002: Re-summarize button visibility

```gherkin
Given a meeting with status "done" and summary_json is populated
When the user views the meeting detail page
Then the summary is displayed in a card below the transcript
And the button text is "Re-summarize" instead of "Summarize"
```

### AC-UI-003: Loading state during summarization

```gherkin
Given the user has clicked the "Summarize" button
When the API call is in progress
Then the button shows a loading spinner
And the button is disabled
```

### AC-UI-004: Summary display after success

```gherkin
Given the summarize API returns successfully
When the response is received
Then the summary card appears below the transcript
And the button text changes to "Re-summarize"
And the meeting query cache is invalidated
```

### AC-UI-005: Error feedback on failure

```gherkin
Given the summarize API returns an error (e.g., HTTP 502)
When the error is received
Then a toast notification displays the error message
And the button returns to its enabled state
```

### AC-UI-006: No summarize button without transcript

```gherkin
Given a meeting with status "done" but no transcript_text
When the user views the meeting detail page
Then no "Summarize" button is shown
```

### AC-UI-007: No summarize button for non-done meetings

```gherkin
Given a meeting with status "recording"
When the user views the meeting detail page
Then no "Summarize" button is shown
```

### AC-UI-008: i18n coverage

```gherkin
Given the portal is displayed in Dutch
When the user views a meeting with a summary
Then all labels use Dutch translations from nl.json
And switching to English shows English translations from en.json
```

---

## Quality Gates

### Definition of Done

- [ ] All acceptance criteria above pass
- [ ] `transcript_filter.py` has unit tests covering all noise patterns (>= 95% coverage)
- [ ] `summarizer.py` has unit tests with mocked LiteLLM responses
- [ ] Integration test: webhook -> segment fetch -> filter -> store -> summarize -> verify stored summary
- [ ] Alembic migration applies cleanly on a fresh database and via upgrade
- [ ] i18n keys present in both `nl.json` and `en.json`
- [ ] No ruff/lint warnings in changed files
- [ ] Frontend builds without TypeScript errors
