# SPEC-BOT-001: Implementation Plan

## SPEC Reference

- **SPEC ID:** SPEC-BOT-001
- **Title:** Calendar Invite Bot Auto-Join via IMAP
- **Status:** Draft
- **Priority:** High

---

## Milestones

### Primary Goal: Core Pipeline (iCal Parse + Tenant Match + Bot Join)

**Objective:** End-to-end flow from iCal content to a scheduled bot join.

Tasks:
1. Add `ical_uid` column to VexaMeeting model and create Alembic migration
2. Implement `ical_parser.py` -- parse iCal bytes, extract VEVENT fields, return `ParsedInvite` dataclass
3. Implement `tenant_matcher.py` -- Zitadel email lookup with 5-minute cache
4. Implement `invite_scheduler.py` -- asyncio-based scheduler, in-memory task tracking, VexaMeeting creation, `start_bot()` dispatch
5. Unit tests for iCal parser with sample .ics files from Google Calendar, Outlook, Apple Calendar
6. Unit tests for tenant matcher (mocked Zitadel API)
7. Unit tests for invite scheduler (mocked VexaClient)

### Secondary Goal: IMAP Listener + Startup Integration

**Objective:** Poll IMAP inbox and wire everything together.

Tasks:
1. Implement `imap_listener.py` -- IMAP4_SSL connection, UNSEEN polling, MIME parsing, exponential backoff
2. Add IMAP config fields to `app.core.config.Settings`
3. Wire listener startup in `app.main` lifespan (conditional on config presence)
4. Integration test: feed raw email bytes to listener pipeline, verify VexaMeeting created
5. Test graceful degradation when IMAP config is missing

### Final Goal: Cancellation Handling + Edge Cases

**Objective:** Handle METHOD:CANCEL and edge cases robustly.

Tasks:
1. Implement cancellation flow in `invite_scheduler.py` -- cancel scheduled task, stop running bot
2. Handle past/imminent meetings (DTSTART already passed)
3. Handle emails with no .ics attachment (ignore gracefully)
4. Handle malformed iCal content (log warning, skip)
5. Tests for cancellation scenarios
6. Tests for all edge cases

### Optional Goal: Internal Test Endpoint

**Objective:** Developer convenience for testing the pipeline.

Tasks:
1. Create `POST /api/internal/invites/test` endpoint
2. Accept raw iCal text, run full pipeline
3. Protect with internal API key
4. Manual testing documentation

---

## Technical Approach

### Architecture Decisions

1. **asyncio over APScheduler:** The portal already uses asyncio background tasks (see `bot_poller.py` pattern). Using `asyncio.create_task()` with `asyncio.sleep()` is simpler and avoids adding APScheduler as a dependency. Trade-off: scheduled tasks are lost on restart (acceptable for MVP).

2. **imaplib with asyncio.to_thread:** `imaplib` is synchronous. Wrapping calls in `asyncio.to_thread()` keeps the event loop unblocked without introducing an async IMAP library dependency.

3. **In-memory task tracking:** Scheduled joins are tracked in a `dict[str, asyncio.Task]` keyed by `ical_uid`. This enables cancellation. Trade-off: lost on restart. Future improvement: persist to DB.

4. **Tenant matching via Zitadel API:** Zitadel is the source of truth for user emails. Direct DB query would bypass Zitadel's auth model. Cache results for 5 minutes to avoid excessive API calls.

5. **Reuse existing `parse_meeting_url()`:** The Vexa service already has URL parsing with platform detection. Reuse it rather than duplicating regex patterns.

### File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `klai-portal/backend/app/models/meetings.py` | Modify | Add `ical_uid` column |
| `klai-portal/backend/app/services/imap_listener.py` | New | IMAP polling service |
| `klai-portal/backend/app/services/ical_parser.py` | New | iCal parsing and URL extraction |
| `klai-portal/backend/app/services/invite_scheduler.py` | New | Bot join scheduling |
| `klai-portal/backend/app/services/tenant_matcher.py` | New | Zitadel email-to-user lookup |
| `klai-portal/backend/app/core/config.py` | Modify | Add IMAP config fields |
| `klai-portal/backend/app/main.py` | Modify | Start IMAP listener in lifespan |
| `klai-portal/backend/app/api/internal_invites.py` | New (optional) | Test endpoint |
| `klai-portal/backend/alembic/versions/xxx_add_ical_uid.py` | New | Migration |
| `klai-portal/backend/pyproject.toml` | Modify | Add `icalendar` dependency |
| `klai-portal/backend/tests/test_ical_parser.py` | New | Parser tests |
| `klai-portal/backend/tests/test_invite_scheduler.py` | New | Scheduler tests |
| `klai-portal/backend/tests/test_tenant_matcher.py` | New | Matcher tests |
| `klai-portal/backend/tests/test_imap_listener.py` | New | Listener tests |
| `klai-portal/backend/tests/fixtures/` | New | Sample .ics files |

### Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Scheduled joins lost on restart | Log all scheduled joins; future: DB persistence with startup recovery sweep |
| iCal format variations | Test with .ics samples from Google, Outlook, Apple; log unparseable content |
| IMAP connection instability | Exponential backoff with max 60s; reconnect and re-scan UNSEEN |
| Zitadel API unavailable | Cache previous results; retry with backoff; degrade gracefully (skip invite) |

---

## Expert Consultation Recommendations

- **expert-backend:** Recommended for architecture review -- asyncio task lifecycle, IMAP connection management, Zitadel API integration patterns
- **expert-devops:** Recommended when deploying -- IMAP inbox provisioning, environment variable management, monitoring setup
