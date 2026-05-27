# Implementation Plan — SPEC-KLAI-ASSISTANT-001

**SPEC:** SPEC-KLAI-ASSISTANT-001 — Klai-specific in-app assistant hub for help, feedback, and problem reports
**Status:** draft
**Author:** Codex
**Created:** 2026-05-27

---

## Overview

Klai needs a low-friction way for logged-in users to ask for help, give product feedback, and report problems from inside the Klai portal. The existing customer-facing agent/widget pattern is useful as interaction model, but the Klai in-app assistant must be a separate first-party surface: it belongs to Klai itself, not to customer-configured widgets embedded in customer products.

The proposed product shape is one persistent Klai launcher in the portal shell. The launcher opens a compact hub with three intent routes:

1. **Stel een vraag** — help/docs/support assistant for using Klai.
2. **Geef feedback** — product feedback intake for ideas, wishes, confusion, and improvement suggestions.
3. **Meld een probleem** — bug/support intake with automatic technical context.

The assistant may reuse chat and agent infrastructure, but it must not reuse the public embeddable widget configuration model as product concept.

## Goals

- Give every logged-in Klai user one obvious place to reach Klai from anywhere in the portal.
- Make feedback submission feel lighter than opening a form or emailing support.
- Convert conversational input into structured internal records instead of storing only transcripts.
- Keep product feedback, problem reports, and help conversations distinct enough for triage and analytics.
- Preserve a clean architectural boundary between first-party Klai assistant and customer-owned widgets.

## Non-Goals

- Do not add these options to customer embeddable widgets.
- Do not expose this assistant through public widget IDs, embed snippets, or public bot share links.
- Do not build chat-answer rating in this SPEC. That is useful later, but separate from product feedback intake.
- Do not require a full external feedback portal for the first version.
- Do not make the assistant configurable by customer admins.

## Existing Context

The repository already contains the pieces that make this feasible:

- `klai-widget` implements the embeddable customer widget with chat chrome, starters, streaming, and public widget config.
- Portal has a React chat surface for widget preview/public bot flows in `klai-portal/frontend/src/features/widgets/chat/WidgetChatSurface.tsx`.
- Partner API has a feedback endpoint at `/partner/v1/feedback`, but it is tied to partner/widget auth, rating semantics, and knowledge-quality feedback.
- Widget session auth explicitly grants `chat: true` and `feedback: false`, which is correct for the current customer-widget path.

This SPEC should introduce a first-party app assistant path rather than bending those partner/widget semantics.

## UX Direction

### Launcher

Use one persistent launcher in the authenticated Klai portal. It should feel like part of the Klai product, not like a third-party chat plugin.

Recommended behavior:

- Fixed bottom-right on desktop.
- Bottom safe-area aware on mobile.
- Opens a panel, not a tiny menu.
- Keeps a stable entrypoint across app pages.
- Can show a subtle unread/status indicator later, but not required for MVP.

### Hub Home

The first screen is a three-option hub, not an immediate empty chat.

Suggested Dutch labels:

- **Stel een vraag**
  - Supporting text: "Hulp bij Klai, instellingen en workflows."
- **Geef feedback**
  - Supporting text: "Deel een idee, wens of verbetering."
- **Meld een probleem**
  - Supporting text: "Laat weten wat niet goed werkt."

The labels should stay action-oriented. Avoid generic labels like "Support", "Contact", or "Chat".

### Route 1: Stel een vraag

User intent: "I need help using Klai."

Flow:

1. Open chat mode with Klai help assistant.
2. Include current route and coarse app context in the system/server context.
3. Let the user chat naturally.
4. Offer escalation to feedback/problem report only when the conversation indicates it.

Expected context:

- Current URL/path.
- User/org/workspace IDs.
- Locale.
- Enabled products/feature flags where useful.
- Role/admin status where useful.

Output:

- Chat transcript and analytics event.
- No product-feedback item unless user explicitly routes into feedback.

### Route 2: Geef Feedback

User intent: "I want to tell Klai what could be better."

Flow:

1. Ask what kind of feedback it is using chips:
   - `Idee`
   - `Verbetering`
   - `Verwarrend`
   - `Mist iets`
   - `Compliment`
2. Ask for the feedback in one open text box.
3. Optionally ask one follow-up question if the input is too vague.
4. Show a concise summary before submit:
   - title
   - category
   - summary
   - page/context
5. User clicks `Versturen`.

Rules:

- Keep the default path to submission under 30 seconds.
- Do not force priority/severity on normal feedback.
- Do not pretend feedback is a live support conversation.
- Allow free-form text without requiring the user to choose chips first.

Output:

- Structured feedback item.
- Product event.
- Optional internal notification.

Recommended data shape:

```ts
interface KlaiFeedbackSubmission {
  type: "idea" | "improvement" | "confusing" | "missing" | "compliment" | "other";
  title: string;
  summary: string;
  raw_text: string;
  page_url: string;
  route_id?: string;
  user_id: string;
  org_id: number;
  workspace_url?: string;
  locale: string;
  created_at: string;
  transcript_id?: string;
}
```

### Route 3: Meld Een Probleem

User intent: "Something is broken or blocking me."

Flow:

1. Ask for the problem in one text box.
2. Ask "Kun je nog verder werken?" using a small severity selector:
   - `Geblokkeerd`
   - `Kan doorwerken`
   - `Klein probleem`
3. Collect automatic context silently.
4. Optional: let the user attach screenshot or include current page screenshot in a later phase.
5. Show summary and submit.

Rules:

- Treat this as support/bug intake, not product feedback.
- Capture diagnostic context automatically; do not ask users for browser/version manually if the app can collect it.
- Never include sensitive page contents by default. Any screenshot/full DOM capture must be explicit.

Output:

- Structured problem report.
- Product/support event.
- Optional Slack/Linear/GitHub integration later.

Recommended data shape:

```ts
interface KlaiProblemReport {
  severity: "blocked" | "workaround" | "minor";
  title: string;
  summary: string;
  raw_text: string;
  page_url: string;
  route_id?: string;
  user_id: string;
  org_id: number;
  workspace_url?: string;
  browser: string;
  viewport: string;
  app_version?: string;
  request_trace_id?: string;
  created_at: string;
  transcript_id?: string;
}
```

## Voice Input

Voice should be treated as a convenience input method, not as a separate product surface in the first release.

Recommended MVP:

- Add a microphone affordance inside feedback/problem text input only if browser permissions and transcription infrastructure are already acceptable.
- Convert speech to text before submit.
- Store the transcript text, not raw audio, unless there is a clear privacy and retention policy.

Do not make "Spreek in" one of the primary three hub options for MVP. It competes with user intent. Voice is a modality; `Geef feedback` and `Meld een probleem` are intents.

## Architecture Decision

### Separate First-Party Assistant From Customer Widget

Create a first-party portal assistant module.

Do:

- Render only inside authenticated Klai portal shell.
- Use app auth/session, not public widget JWTs.
- Use fixed Klai-owned configuration server-side.
- Use app endpoints such as `/api/app/assistant/*`, `/api/app/feedback`, and `/api/app/problem-reports`.
- Store submissions in first-party Klai tables or forward to internal tooling through a backend service.

Do not:

- Add this as a generic `klai-widget` feature.
- Add it to widget admin settings.
- Expose it through `/partner/v1/widget-config`.
- Reuse `/partner/v1/feedback` for product feedback. That endpoint is knowledge-answer feedback with rating semantics.

### Proposed Frontend Boundary

New module:

```text
klai-portal/frontend/src/features/klai-assistant/
  KlaiAssistantLauncher.tsx
  KlaiAssistantPanel.tsx
  KlaiAssistantHome.tsx
  HelpChatView.tsx
  FeedbackIntakeView.tsx
  ProblemReportView.tsx
  assistant-context.ts
  types.ts
```

Mount point:

- Portal authenticated layout/shell, close to other global UI concerns.

State:

- Local panel state for MVP.
- Persist draft feedback/problem text in memory only.
- Later: persist assistant conversations if product requirements need history.

### Proposed Backend Boundary

New app endpoints:

```text
POST /api/app/assistant/chat
POST /api/app/feedback
POST /api/app/problem-reports
```

Possible service modules:

```text
klai-portal/backend/app/api/app_assistant.py
klai-portal/backend/app/services/app_assistant.py
klai-portal/backend/app/services/product_feedback.py
klai-portal/backend/app/services/problem_reports.py
```

Storage options:

1. **Portal DB tables** for first-party records.
   - Best for ownership and analytics.
   - Requires migrations and admin/internal views later.
2. **External issue/feedback tool integration** only.
   - Faster if Linear/GitHub/Slack is already the triage destination.
   - Risk: weaker product analytics and harder dedupe.
3. **Hybrid.**
   - Store canonical record in Portal DB, forward notification/integration asynchronously.
   - Recommended.

## Milestones

### Phase 1 — Product Shape and Static UI

Deliverable: first-party Klai assistant launcher and hub behind a feature flag.

Tasks:

1. Add `KlaiAssistantLauncher` to authenticated portal shell.
2. Implement panel home with the three intent tiles.
3. Implement empty static views for help, feedback, and problem report.
4. Add Paraglide messages for NL and EN.
5. Gate behind a feature flag or environment switch.
6. Verify responsive behavior on desktop and mobile.

Acceptance:

- The launcher appears only for logged-in Klai portal users.
- It does not appear in public bot pages, customer widget previews, or embeddable widget bundles.
- The first panel screen shows the three Klai-specific options.

### Phase 2 — Feedback and Problem Intake MVP

Deliverable: users can submit structured product feedback and problem reports.

Tasks:

1. Add backend models/tables or service storage decision.
2. Implement `POST /api/app/feedback`.
3. Implement `POST /api/app/problem-reports`.
4. Add frontend feedback intake flow.
5. Add frontend problem report flow.
6. Capture route/user/org/browser context server-side where possible.
7. Emit product events for submitted feedback/problem reports.
8. Add unit tests for request validation and auth.

Acceptance:

- Feedback creates a structured record with type, summary/raw text, user, org, and page URL.
- Problem report creates a separate structured record with severity and diagnostic context.
- Anonymous/public callers cannot submit.
- Customer widget sessions cannot use these endpoints.

### Phase 3 — Help Assistant Chat

Deliverable: `Stel een vraag` opens a Klai-owned help assistant.

Tasks:

1. Define the server-side Klai assistant prompt and allowed knowledge sources.
2. Implement `POST /api/app/assistant/chat`.
3. Reuse existing streaming UI patterns where practical.
4. Include current page/app context in the assistant request.
5. Add escalation actions from help chat into feedback/problem flows.
6. Add tests for auth, context shaping, and failure handling.

Acceptance:

- Help chat answers questions about Klai.
- Help chat cannot mutate customer data.
- The assistant can route users to feedback/problem intake without losing their typed context.

### Phase 4 — Internal Triage Surface

Deliverable: Klai team can review incoming records.

Tasks:

1. Add a basic internal/admin list for feedback submissions.
2. Add a basic internal/admin list for problem reports.
3. Add status fields:
   - feedback: `new`, `reviewed`, `planned`, `closed`
   - problem: `new`, `triaged`, `fixed`, `closed`
4. Add optional assignee/source fields if needed.
5. Consider forwarding to Slack/Linear/GitHub.

Acceptance:

- Klai team can distinguish feedback from problems.
- Records include enough context to follow up with the user.
- Status changes are auditable or at least timestamped.

### Phase 5 — Enhancements

Optional later work:

1. Voice-to-text input for feedback/problem reports.
2. Screenshot attachment with explicit user consent.
3. Duplicate detection and clustering for feedback themes.
4. Changelog/release-note callbacks: "we shipped something you asked for."
5. Chat answer rating for the help assistant.
6. Deep links:
   - `?assistant=feedback`
   - `?assistant=problem`
   - `?assistant=help`

## File Changes Summary

| File/Area | Change Type | Description |
|---|---|---|
| `klai-portal/frontend/src/features/klai-assistant/*` | New | First-party assistant UI module |
| Portal authenticated layout/shell | Modify | Mount launcher behind flag |
| `klai-portal/frontend/messages/*.json` | Modify | Add NL/EN copy |
| `klai-portal/backend/app/api/app_assistant.py` | New | App assistant, feedback, problem endpoints |
| `klai-portal/backend/app/services/product_feedback.py` | New | Feedback persistence/integration service |
| `klai-portal/backend/app/services/problem_reports.py` | New | Problem report persistence/integration service |
| Portal DB migration | New | Feedback/problem tables if DB-backed |
| Backend tests | New | Auth, validation, storage tests |
| Frontend tests | New | Hub routing and intake flow tests |

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Users confuse help chat with product feedback | Start with a hub and distinct labels; keep feedback/problem flows structured |
| Feedback becomes unstructured chat transcript noise | Always create structured records with summary, type, raw text, user/org/page context |
| First-party assistant leaks into customer widgets | Separate modules/endpoints/auth; add tests or bundle checks |
| Problem reports lack diagnostic context | Capture URL, org, user, browser, viewport, and trace IDs automatically |
| Voice input raises privacy concerns | Ship text-first MVP; add voice later with transcript-only default and explicit consent |
| Internal team gets overwhelmed | Add status, category, and severity from day one; forward only high-signal events initially |

## Validation Commands

Exact commands depend on implementation choices, but expected validation should include:

```bash
cd klai-portal/frontend && npm test -- klai-assistant
cd klai-portal/backend && uv run pytest tests/test_app_assistant.py -q
git diff --check
```

For UI implementation:

```bash
cd klai-portal/frontend && npm run lint
cd klai-portal/frontend && npm run test
```

## Rollout

1. Ship behind an internal feature flag.
2. Enable for Klai team users first.
3. Review first 20-50 submissions for category quality.
4. Adjust copy/chips before enabling for all customers.
5. Add triage forwarding only after the record shape is stable.

## Open Questions

1. Should feedback/problem records live only in Portal DB, or also sync immediately to Linear/GitHub/Slack?
2. Which internal team owns `Geef feedback` triage versus `Meld een probleem` triage?
3. Should users receive an email or in-app acknowledgement after submission?
4. Should screenshots be part of MVP or deferred?
5. Which Klai knowledge sources should the help assistant use for `Stel een vraag`?
6. Should the assistant launcher be visible to all roles, or hidden for some restricted users?
