# SPEC-BOT-001: Acceptance Criteria

## SPEC Reference

- **SPEC ID:** SPEC-BOT-001
- **Title:** Calendar Invite Bot Auto-Join via IMAP

---

## Test Scenarios

### Scenario 1: Happy Path -- Google Meet Invite

```gherkin
Given a registered portal user with email "alice@company.com"
  And the user sends a Google Calendar invite to "meet@getklai.com"
  And the invite contains a .ics attachment with:
    | Field              | Value                                    |
    | UID                | abc123@google.com                        |
    | DTSTART            | 2025-06-15T14:00:00Z (30 minutes from now)|
    | ORGANIZER          | mailto:alice@company.com                 |
    | X-GOOGLE-CONFERENCE| https://meet.google.com/abc-defg-hij     |
    | SUMMARY            | Weekly Standup                            |
When the IMAP listener polls and finds this UNSEEN email
Then the system parses the iCal and extracts the meeting URL
  And the system matches "alice@company.com" to a PortalUser via Zitadel
  And the system schedules a bot join for DTSTART minus 60 seconds
  And the email is marked as SEEN
  And a VexaMeeting record is created with:
    | Field              | Value                                    |
    | ical_uid           | abc123@google.com                        |
    | platform           | google_meet                              |
    | meeting_url        | https://meet.google.com/abc-defg-hij     |
    | status             | scheduled                                |
    | consent_given      | true                                     |
    | meeting_title      | Weekly Standup                            |
```

### Scenario 2: Happy Path -- Zoom Invite

```gherkin
Given a registered portal user with email "bob@company.com"
  And the user sends a Zoom invite to "meet@getklai.com"
  And the .ics DESCRIPTION contains "https://us05web.zoom.us/j/1234567890"
  And DTSTART is 2 hours from now
When the IMAP listener processes the email
Then the system extracts the Zoom URL from DESCRIPTION via regex
  And platform is set to "zoom"
  And a bot join is scheduled for DTSTART minus 60 seconds
```

### Scenario 3: Happy Path -- Microsoft Teams Invite

```gherkin
Given a registered portal user with email "carol@company.com"
  And the user sends a Teams invite to "meet@getklai.com"
  And the .ics contains X-MICROSOFT-SKYPETEAMSMEETINGURL
When the IMAP listener processes the email
Then the system extracts the Teams URL from the X-MICROSOFT property
  And platform is set to "teams"
  And a bot join is scheduled
```

### Scenario 4: Unregistered Sender

```gherkin
Given an email from "stranger@unknown.com" with a valid .ics attachment
  And "stranger@unknown.com" does not match any PortalUser in Zitadel
When the IMAP listener processes the email
Then the system logs INFO: "Ignoring invite from unregistered sender: stranger@unknown.com"
  And no VexaMeeting is created
  And no bot join is scheduled
  And the email is marked as SEEN
```

### Scenario 5: Duplicate Invite (Same ical_uid)

```gherkin
Given a VexaMeeting already exists with ical_uid "abc123@google.com"
  And a new email arrives with the same ical_uid
When the IMAP listener processes the email
Then the system logs DEBUG: duplicate ical_uid detected
  And no new VexaMeeting is created
  And no additional bot join is scheduled
  And the email is marked as SEEN
```

### Scenario 6: Meeting Cancellation

```gherkin
Given a scheduled VexaMeeting exists with ical_uid "abc123@google.com" and status "scheduled"
  And a cancellation email arrives with METHOD:CANCEL and UID "abc123@google.com"
When the IMAP listener processes the cancellation
Then the system cancels the scheduled asyncio join task
  And the VexaMeeting status is updated to "cancelled"
  And the email is marked as SEEN
```

### Scenario 7: Cancellation While Bot Is Running

```gherkin
Given a VexaMeeting exists with ical_uid "abc123@google.com" and status "in_progress"
  And a cancellation email arrives with METHOD:CANCEL and UID "abc123@google.com"
When the IMAP listener processes the cancellation
Then the system calls vexa_client.stop_bot() for the meeting
  And the VexaMeeting status is updated to "cancelled"
```

### Scenario 8: No Meeting URL in iCal

```gherkin
Given an email with a valid .ics attachment
  And the iCal VEVENT has no CONFERENCE property, no X-GOOGLE-CONFERENCE, no X-MICROSOFT property
  And the DESCRIPTION does not contain any recognized meeting URL pattern
When the IMAP listener processes the email
Then the system logs WARNING with the email subject and sender
  And no VexaMeeting is created
  And the email is marked as SEEN
```

### Scenario 9: Meeting Already Started (Past DTSTART)

```gherkin
Given an email with a valid .ics attachment
  And DTSTART is 10 minutes in the past
When the IMAP listener processes the email
Then the system logs INFO: "Ignoring past/imminent meeting"
  And no bot join is scheduled
  And the email is marked as SEEN
```

### Scenario 10: Meeting Starts Within 60 Seconds

```gherkin
Given an email with a valid .ics attachment
  And DTSTART is 30 seconds from now
When the IMAP listener processes the email
Then the system treats it as imminent and ignores it
  And no bot join is scheduled
```

### Scenario 11: IMAP Config Missing

```gherkin
Given IMAP_HOST is not set in environment variables
When the portal application starts
Then the system logs WARNING: "IMAP listener disabled: missing configuration"
  And the IMAP listener does not start
  And all other portal functionality works normally
```

### Scenario 12: IMAP Connection Failure

```gherkin
Given the IMAP server is unreachable
When the IMAP listener attempts to connect
Then the system logs WARNING with the connection error
  And retries with exponential backoff: 1s, 2s, 4s, 8s, up to 60s max
  And resumes polling after successful reconnection
```

### Scenario 13: Malformed iCal Content

```gherkin
Given an email with a text/calendar MIME part
  And the content is not valid iCal (parsing throws an exception)
When the IMAP listener processes the email
Then the system logs WARNING: "Failed to parse iCal content"
  And the email is marked as SEEN
  And processing continues with next email
```

### Scenario 14: Bot Join Execution

```gherkin
Given a scheduled bot join for ical_uid "abc123@google.com" at 13:59:00 UTC
  And the meeting DTSTART is 14:00:00 UTC
When the current time reaches 13:59:00 UTC
Then the system creates a VexaMeeting with status "pending"
  And calls vexa_client.start_bot(platform="google_meet", native_meeting_id="abc-defg-hij")
  And updates VexaMeeting status to "in_progress" and stores the bot_id
```

### Scenario 15: Bot Join Failure

```gherkin
Given a scheduled bot join triggers
  And vexa_client.start_bot() raises an exception
When the bot join fails
Then the VexaMeeting status is set to "error"
  And the error_message field contains the exception details
  And a WARNING log is emitted
```

---

## Quality Gates

| Gate | Criterion | Threshold |
|------|-----------|-----------|
| Unit Test Coverage | All new services (parser, scheduler, matcher, listener) | >= 85% line coverage |
| Integration Test | End-to-end pipeline with mocked IMAP and Vexa | All scenarios pass |
| iCal Compatibility | Tested with .ics from Google Calendar, Outlook, Apple Calendar | All 3 parse correctly |
| Error Handling | All failure modes logged with appropriate level | No unhandled exceptions |
| Migration | Alembic upgrade/downgrade works cleanly | Reversible migration |
| Existing Tests | All existing portal tests continue to pass | Zero regressions |

---

## Verification Methods

| Method | Tool | Scope |
|--------|------|-------|
| Unit tests | pytest + pytest-asyncio | Parser, scheduler, matcher |
| Integration tests | pytest with mocked IMAP server | Full pipeline |
| Manual testing | Internal test endpoint + real IMAP | End-to-end with real email |
| Migration testing | `alembic upgrade head` / `alembic downgrade -1` | Schema changes |
| Compatibility testing | Sample .ics files from 3 calendar providers | Parser robustness |

---

## Definition of Done

- [ ] All 15 acceptance scenarios pass as automated tests
- [ ] `ical_uid` column added with reversible Alembic migration
- [ ] IMAP listener starts conditionally and degrades gracefully
- [ ] Cancellation handling works for both scheduled and in-progress meetings
- [ ] No regressions in existing portal test suite
- [ ] Code reviewed and merged to main branch
- [ ] `icalendar` dependency added to `pyproject.toml`
- [ ] Structured logging covers all success and failure paths
