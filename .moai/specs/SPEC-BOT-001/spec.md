# SPEC-BOT-001: Calendar Invite Bot Auto-Join via IMAP

## Status

Draft

## Problem Statement

Users currently must manually trigger bot joins for each meeting through the portal UI. This creates friction and requires users to remember to start the bot before every meeting. Users already invite participants via their calendar app -- if they could simply invite `meet@getklai.com`, the bot would join automatically at the scheduled time with zero portal interaction.

## User Story

As a portal user, I want to invite `meet@getklai.com` to any Google Meet, Zoom, or Teams meeting via my calendar app, so that the Klai bot automatically joins the meeting at the scheduled time and the recording and transcript appear in my portal without manual intervention.

## Scope

### In Scope

- IMAP polling of `meet@getklai.com` inbox for calendar invites
- Parsing RFC 5545 iCal (.ics) attachments from invite emails
- Extracting meeting URL, start time, and organizer email from iCal data
- Matching organizer email to a PortalUser via Zitadel user search
- Scheduling bot join at DTSTART minus 60 seconds
- Handling `METHOD:CANCEL` to cancel scheduled joins
- Duplicate prevention via iCal UID
- New `ical_uid` column on `VexaMeeting` model with Alembic migration
- IMAP configuration via environment variables
- Logging and error handling for all failure modes

### Out of Scope

- Replying to unregistered senders (deferred to future iteration)
- Recurring meeting series expansion (RRULE) -- only the first occurrence is processed
- Calendar invite updates (`METHOD:REQUEST` with changed time) -- deferred to SPEC-BOT-002
- Web UI for managing invite-based meetings (uses existing meeting list)
- SMTP reply confirming bot will join (deferred)
- OAuth-based calendar integration (this SPEC uses IMAP only)
- Support for calendar apps that do not send standard .ics attachments

---

## Acceptance Criteria

### IMAP Polling

**AC-1:** WHEN the IMAP listener starts, THE SYSTEM SHALL connect to the configured IMAP server using `IMAP_HOST`, `IMAP_PORT`, `IMAP_USERNAME`, and `IMAP_PASSWORD` from environment variables.

**AC-2:** WHILE the IMAP listener is running, THE SYSTEM SHALL poll for UNSEEN emails every `IMAP_POLL_INTERVAL_SECONDS` seconds (default: 60).

**AC-3:** WHEN an email is processed (success or ignored), THE SYSTEM SHALL mark it as SEEN so it is not reprocessed.

**AC-4:** IF the IMAP connection fails, THEN THE SYSTEM SHALL retry with exponential backoff (1s, 2s, 4s, 8s, max 60s) and log each failure at WARNING level.

### iCal Parsing

**AC-5:** WHEN an email contains a `text/calendar` MIME part or an `.ics` file attachment, THE SYSTEM SHALL parse the iCal content using the `icalendar` library.

**AC-6:** WHEN the iCal contains a `VEVENT` component, THE SYSTEM SHALL extract:
- `UID` (iCal unique identifier)
- `DTSTART` (meeting start time, converted to UTC)
- `ORGANIZER` (email address of the meeting creator)
- `SUMMARY` (meeting title, optional)
- `DESCRIPTION` (full text, for URL extraction)
- `CONFERENCE` / `X-GOOGLE-CONFERENCE` / `X-MICROSOFT-SKYPETEAMSMEETINGURL` properties

**AC-7:** IF the iCal `METHOD` is `CANCEL`, THEN THE SYSTEM SHALL handle it as a cancellation (see AC-16, AC-17).

### Meeting URL Extraction

**AC-8:** WHEN extracting the meeting URL, THE SYSTEM SHALL check sources in this priority order:
1. `CONFERENCE` property value
2. `X-GOOGLE-CONFERENCE` property value
3. `X-MICROSOFT-SKYPETEAMSMEETINGURL` property value
4. Regex match in `DESCRIPTION` for patterns:
   - `https://meet.google.com/[a-z0-9-]+`
   - `https://[\w-]+.zoom.us/j/\d+`
   - `https://teams.microsoft.com/l/meetup-join/...`

**AC-9:** THE SYSTEM SHALL use the existing `parse_meeting_url()` function from `app.services.vexa` to validate the extracted URL and determine the platform (`google_meet`, `zoom`, `teams`).

**AC-10:** IF no valid meeting URL is found in the iCal event, THEN THE SYSTEM SHALL ignore the invite and log a WARNING with the email subject and sender.

### Tenant Matching & Authorization

**AC-11:** WHEN a valid iCal invite is parsed, THE SYSTEM SHALL extract the organizer's email from the `ORGANIZER` field (stripping the `mailto:` prefix).

**AC-12:** THE SYSTEM SHALL query Zitadel's user search API to find a PortalUser whose email matches the organizer email.

**AC-13:** IF no matching PortalUser is found, THEN THE SYSTEM SHALL silently ignore the invite and log an INFO message: "Ignoring invite from unregistered sender: {email}".

**AC-14:** IF a matching PortalUser is found, THE SYSTEM SHALL perform the following authorization checks in order — any failure results in silent ignore + INFO log:

**AC-14a (Plan check):** THE SYSTEM SHALL verify that the user's `PortalOrg.plan` is one of the plans that includes the scribe feature (`professional`, `complete`). Having scribe is sufficient authorization — no separate opt-in is required.

**AC-14b (Rate limit check):** THE SYSTEM SHALL verify that the user has not exceeded `INVITE_BOT_RATE_LIMIT_PER_USER_PER_DAY` (default: 10) invite-triggered bot joins in the current UTC day. If exceeded, log WARNING: "Rate limit exceeded for user {zitadel_user_id}".

**AC-15:** IF all authorization checks pass, THE SYSTEM SHALL use that user's `zitadel_user_id` and `org_id` for the VexaMeeting record.

### Duplicate Prevention

**AC-16:** WHEN creating a VexaMeeting from an iCal invite, THE SYSTEM SHALL check if a VexaMeeting with the same `ical_uid` already exists. IF it does, THE SYSTEM SHALL skip processing and log a DEBUG message.

### Cancellation Handling

**AC-17:** WHEN an iCal with `METHOD:CANCEL` is received, THE SYSTEM SHALL look up the VexaMeeting by `ical_uid`.

**AC-18:** IF a matching VexaMeeting is found:
- IF status is `scheduled`: cancel the scheduled join task and set status to `cancelled`.
- IF status is `in_progress` (bot already joined): call `vexa_client.stop_bot()` and set status to `cancelled`.
- IF status is `completed` or `cancelled`: ignore (no action needed).

### Bot Join Scheduling

**AC-19:** WHEN a valid, authorized, non-duplicate invite is processed, THE SYSTEM SHALL schedule a bot join task for `DTSTART - 60 seconds`.

**AC-20:** IF the calculated join time is in the past (meeting already started or starts within 60 seconds), THEN THE SYSTEM SHALL ignore the invite and log an INFO message: "Ignoring past/imminent meeting: {subject} starting at {dtstart}".

**AC-21:** WHEN the scheduled join time arrives, THE SYSTEM SHALL:
1. Create a `VexaMeeting` record with status `pending`, the extracted `ical_uid`, `meeting_url`, `platform`, `native_meeting_id`, `zitadel_user_id`, `org_id`, and `meeting_title`.
2. Call `vexa_client.start_bot(platform, native_meeting_id)` to dispatch the bot.
3. Update the VexaMeeting `bot_id` and `status` to `in_progress` on success.
4. On failure, set status to `error` and store the error message.

### Configuration

**AC-22:** THE SYSTEM SHALL read the following environment variables:
- `IMAP_HOST` (required)
- `IMAP_PORT` (optional, default: `993`)
- `IMAP_USERNAME` (required)
- `IMAP_PASSWORD` (required)
- `IMAP_POLL_INTERVAL_SECONDS` (optional, default: `60`)
- `INVITE_BOT_RATE_LIMIT_PER_USER_PER_DAY` (optional, default: `10`)

**AC-23:** IF any required IMAP variable is missing, THE SYSTEM SHALL log a WARNING at startup and disable the IMAP listener (the rest of the portal continues to function normally).

### Observability

**AC-24:** THE SYSTEM SHALL log at structured INFO level for each successfully scheduled bot join, including: `ical_uid`, `organizer_email`, `platform`, `meeting_url`, `scheduled_join_time`.

**AC-25:** THE SYSTEM SHALL log at WARNING level for: IMAP connection failures, unparseable iCal content, missing meeting URLs, authorization failures (rate limit exceeded), and bot join failures.

---

## Technical Design

### Architecture

```
[Calendar App] --invite email--> [IMAP Inbox: meet@getklai.com]
                                         |
                                   [IMAP Listener]
                                   (poll every 60s)
                                         |
                                   [iCal Parser]
                                   (extract URL, time, organizer)
                                         |
                                   [Tenant Matcher]
                                   (Zitadel email lookup)
                                         |
                                   [Invite Scheduler]
                                   (schedule join at DTSTART-60s)
                                         |
                                   [VexaClient.start_bot()]
                                   (existing bot dispatch)
                                         |
                                   [VexaMeeting record]
                                   (transcript + recording in portal)
```

### Data Model Changes

**VexaMeeting** -- add column:

| Column     | Type          | Nullable | Unique | Index | Purpose                    |
|------------|---------------|----------|--------|-------|----------------------------|
| `ical_uid` | `String(512)` | Yes      | Yes    | Yes   | iCal UID for deduplication |

- Nullable because existing user-initiated meetings have no iCal UID.
- Unique constraint enables fast duplicate lookup and prevents race conditions.
- String(512) accommodates long RFC 5545 UID values (e.g., `040000008200E00074C5B7101A82E008...@google.com`).

**Alembic migration:**
- `add_ical_uid_to_vexa_meetings`
- Adds `ical_uid` on `vexa_meetings` (unique, nullable)

### New Components

#### `klai-portal/backend/app/services/imap_listener.py`

Responsibilities:
- Connect to IMAP server using `imaplib.IMAP4_SSL`
- Poll for UNSEEN emails on configurable interval
- Extract `text/calendar` MIME parts and `.ics` attachments using `email` stdlib
- Mark processed emails as SEEN
- Retry connection with exponential backoff
- Run as asyncio background task started from `app.main` lifespan

Key design:
- Uses `asyncio.to_thread()` to wrap blocking `imaplib` calls
- Maintains persistent IMAP connection, reconnects on failure
- Processes emails sequentially to avoid race conditions on duplicate checks

#### `klai-portal/backend/app/services/ical_parser.py`

Responsibilities:
- Parse iCal bytes using `icalendar.Calendar.from_ical()`
- Extract VEVENT components
- Extract organizer email (strip `mailto:` prefix, handle `CN` parameter)
- Extract meeting URL using priority chain (AC-8)
- Determine if method is CANCEL
- Convert DTSTART to UTC datetime

Returns a dataclass:

```python
@dataclass
class ParsedInvite:
    uid: str
    organizer_email: str
    meeting_url: str | None
    platform: str | None
    native_meeting_id: str | None
    dtstart: datetime  # UTC
    summary: str | None
    is_cancellation: bool
```

#### `klai-portal/backend/app/services/invite_scheduler.py`

Responsibilities:
- Schedule bot joins using `asyncio` tasks with `asyncio.sleep()` until join time
- Track scheduled tasks by `ical_uid` in an in-memory dict for cancellation
- On trigger: create VexaMeeting, call `start_bot()`, update status
- On cancellation: cancel asyncio task, update VexaMeeting status
- Handle edge case: if portal restarts, any scheduled-but-not-yet-triggered joins are lost (acceptable for MVP; future: persist scheduled joins to DB)

#### `klai-portal/backend/app/services/tenant_matcher.py`

Responsibilities:
- Query Zitadel Management API to find user by email
- Return `(zitadel_user_id, org_id)` tuple or `None`
- Cache results for 5 minutes to reduce Zitadel API calls
- Use existing Zitadel client from portal backend

### Configuration

Add to `klai-portal/backend/app/core/config.py` (Settings model):

```python
imap_host: str | None = None
imap_port: int = 993
imap_username: str | None = None
imap_password: str | None = None
imap_poll_interval_seconds: int = 60
```

Add to `deploy/docker-compose.yml` under `portal-api` environment (non-secret values only):

```yaml
IMAP_HOST: getklai.com
IMAP_PORT: "993"
IMAP_USERNAME: meet@getklai.com
IMAP_POLL_INTERVAL_SECONDS: "60"
IMAP_PASSWORD: ${PORTAL_API_IMAP_PASSWORD}
```

**Secret storage — `IMAP_PASSWORD` NEVER goes in git:**
- Stored exclusively in `/opt/klai/.env` on the server as `PORTAL_API_IMAP_PASSWORD`
- Added via SOPS or direct append with single quotes: `echo 'PORTAL_API_IMAP_PASSWORD=...' >> /opt/klai/.env`
- Verified after adding: `docker exec klai-core-portal-api-1 printenv IMAP_PASSWORD`
- See `klai-claude/docs/patterns/infrastructure.md#env-modification-rules`

### Startup Integration

In `klai-portal/backend/app/main.py` lifespan:

```python
if settings.imap_host and settings.imap_username:
    from app.services.imap_listener import start_imap_listener
    asyncio.create_task(start_imap_listener())
```

### Internal Test Endpoint (Optional)

`klai-portal/backend/app/api/internal_invites.py`:

- `POST /api/internal/invites/test` -- accepts raw iCal text, runs the full pipeline (parse, match, schedule). Protected by internal API key. For development and integration testing only.

---

## Dependencies

| Dependency          | Type     | Notes                                                    |
|---------------------|----------|----------------------------------------------------------|
| `icalendar`         | PyPI     | iCal parsing library, add to `pyproject.toml`            |
| `imaplib`           | stdlib   | IMAP client, no install needed                           |
| Zitadel Management API | External | Already integrated in portal backend                  |
| Vexa bot-manager    | Internal | Existing `VexaClient.start_bot()` / `stop_bot()`        |
| IMAP inbox          | Infra    | `meet@getklai.com` mailbox, provisioned separately       |
| Alembic             | Existing | For database migration                                   |

---

## Risks & Open Questions

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| IMAP polling misses an email during reconnect | Low | Medium | Mark SEEN only after successful processing; on reconnect, re-scan UNSEEN |
| Scheduled join lost on portal restart | Medium | Medium | MVP accepts this; future: persist scheduled joins to DB with a recovery sweep on startup |
| Zitadel API rate limiting on email lookups | Low | Low | Cache results for 5 minutes; batch lookups if volume grows |
| iCal format variations across calendar apps | Medium | Medium | Test with Google Calendar, Outlook, Apple Calendar; log unparseable content for debugging |
| Timezone handling errors in DTSTART | Low | High | Always convert to UTC immediately; use `icalendar` built-in timezone support |
| Duplicate bot joins from rapid IMAP polls | Low | Medium | `ical_uid` unique constraint + check-before-insert pattern |

### Open Questions

1. **Which plans include scribe?** Currently `professional` and `complete` assumed. `core` excluded. Confirm with product.

3. **Reply to unregistered or unauthorized senders?** Currently silently ignored. Could send a "not registered" or "feature not enabled" reply. Deferred -- requires SMTP + copy review.

4. **Recurring meetings (RRULE)?** This SPEC handles only single-occurrence meetings. Deferred to SPEC-BOT-002.

5. **Meeting updates (changed time)?** Same UID + new DTSTART currently skipped as duplicate. Deferred to SPEC-BOT-002.

6. **Group/org assignment?** Leave `group_id` as NULL for invite-based meetings for now.

7. **Consent flow?** Set `consent_given = True` implicitly for invite-based meetings (inviting the bot is explicit consent).
