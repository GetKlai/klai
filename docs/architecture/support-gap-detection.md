# Support knowledge gap detection

Status: implemented; source access and acceptance gates must be verified for each deployment. Updated: 2026-09-18.

## Decision and scope

Start with a direct, read-only HubSpot integration and learn from actual field availability and complete support cases. A separate export experiment is not a prerequisite. Audio transcripts are a complementary input to the same analysis. This document describes public product architecture; customer records, recordings, credentials, and production access procedures stay outside this repository.

The objective is to identify actionable improvements to support knowledge, with source evidence and human editorial ownership. Implementation follows the contract below; automatic publication is excluded.

## Research findings

The [KCS capture practice](https://library.serviceinnovation.org/KCS/Knowledge-Centered_Success_Practices_Guide/201-Solve_Loop/Practice_3_Capture/Technique_3.1) treats customer wording, context, diagnostic questions, and resolution as knowledge captured during support work. The unit of analysis should therefore be a question in a complete case, not an isolated message or ticket title.

[KCS self-service gap analysis](https://library.serviceinnovation.org/KCS/KCS_v6/KCS_v6_KDA_Guide/060_KDA_Techniques/20_Plugging_Content_Gaps_Identified_From_Self-Service) distinguishes missing content from findability failures. Search analytics also expose unsuccessful attempts that never become tickets.

| Candidate outcome | Evidence needed | Editorial action |
|---|---|---|
| Missing knowledge | A reusable question lacks an answer in the relevant knowledge sources | Create content |
| Incomplete knowledge | Existing content omits a necessary condition, exception, or step | Amend content |
| Outdated or contradictory knowledge | Applicable sources or verified product behavior disagree | Resolve with the content owner |
| Findability failure | Suitable content exists but was not retrieved or found | Improve search, naming, links, or indexing |
| Audience mismatch | The answer assumes access or expertise the reader does not have | Adapt customer guidance or internal procedure |
| Other support need | Resolution needs an account action, incident response, product fix, or further investigation | Route to an owner; document reusable guidance when supported |

## Source value

| Source | Main contribution | Required context |
|---|---|---|
| Complete tickets and email threads | Demand, diagnosis, replies, resolution, recurrence | Stable IDs, ordered messages, notes, outcome, linked articles |
| Support call transcripts | Customer language, clarifications, troubleshooting steps | Source reference, date, language, timing and speakers when available |
| Search and article feedback | Failed self-service, including demand absent from tickets | Search terms, results, clicks, feedback and time window |
| Internal procedures and escalations | Expert knowledge not yet available to the intended audience | Owner, applicability, access restrictions, validation state |
| Product documentation and release history | Correctness and freshness of proposed answers | Product/version, effective date, authoritative owner |
| AI conversations and handoffs | Retrieval/answer failures and information added by a human | Retrieved sources, generated reply, handoff and final outcome |

Current public and internal knowledge collections are the comparison baseline, not automatically interchangeable publication destinations. Customer questions establish demand; validated product information and confirmed resolutions establish answers. Preserve the difference between a general solution, a temporary workaround, and a customer-specific exception.

## Relevant implementations and evidence

- [Intercom content recommendations](https://www.intercom.com/help/en/articles/11394959-use-ai-powered-content-recommendations-to-improve-fin) compare unsuccessful AI replies with human responses, identify missing/duplicate/contradictory content, and show source conversations for review.
- [Zendesk automation potential](https://support.zendesk.com/hc/en-us/articles/9877546283930-Viewing-and-using-the-automation-potential-report-to-create-or-enhance-AI-agents) groups recent solved tickets into topics covered or not covered by existing knowledge. Its documented selection limits mean coverage of input cases must be reported separately from detected topics.
- [HubSpot Knowledge Base Agent](https://knowledge.hubspot.com/knowledge-base/create-a-knowledge-base-agent) already proposes articles from closed tickets and edits to existing content. Compare against available account functionality before duplicating it; access and source coverage require account-level verification.
- [Zhang et al. (2025)](https://arxiv.org/html/2506.17484v1) report 48.74% helpful answers from synthesized knowledge versus 38.60% from raw ticket retrieval. This is a single-domain, model-judged experiment, not measured contact reduction or a forecast for Klai.
- [KCS article structure](https://library.serviceinnovation.org/KCS/KCS_v6/KCS_v6_Practices_Guide/030/020) separates the customer's issue/environment, resolution, and metadata. Use it to keep applicability and editorial state explicit.

## Quality and operating principles

Each proposal must expose source cases/passages, the relevant existing articles, the specific missing information, affected audience, and an accountable editor. Deduplicate repeated messages and channel copies into cases; split distinct questions within a case. Keep incident bursts distinguishable from independent recurring needs.

Prioritize customer impact, unique case frequency, recency, support effort, and whether documentation can help. Retain serious low-frequency cases. Unknown outcomes remain unknown; do not invent an answer to complete a draft.

Evaluate proposal usefulness through editorial decisions, keeping rejection, correction and acceptance distinct. Whole-case human labeling is optional evaluation work, not an ingestion or rollout prerequisite. Without independent case references, missed gaps and recall remain unmeasured. Hold out later cases from tuning. Measure retrieval quality separately from answer correctness and grounding ([Ragas metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)). Check both historical applicability and whether a gap remains open against today's knowledge.

After editorial changes, measure task success and repeat contacts as well as content coverage. A session without a ticket is not proof of success ([KCS self-service measurement](https://library.serviceinnovation.org/KCS/Measuring_Self-Service_Success/20_Why_Measure_Self-Service_Success)). Start with scheduled ingestion and a weekly editorial cadence; adjust frequency from measured need.

## Existing Klai implementation

The following describes source code in this checkout, not a claim that every path is enabled in a customer account.

| Component | Current behavior | Reuse or extension |
|---|---|---|
| [HubSpot draft function](../../klai-hubspot/klai-email-support/src/app/functions/NewFunction.js) | Reads ticket subject/content from card context, generates/revises a draft, stores a support session | Reuse ticket linkage and authenticated partner integration; add independent complete-case ingestion |
| [Partner support sessions](../../klai-portal/backend/app/models/partner_support.py) | Tracks draft-assistance sessions per ticket/user; message roles are agent, assistant, system | Link case evidence to sessions; do not treat draft history as actual customer correspondence or confirmed resolution |
| [Connector adapters](../../klai-connector/app/adapters/base.py) and [sync engine](../../klai-connector/app/services/sync_engine.py) | Adapter enumeration/fetch/cursors, scheduling, status and resume bookkeeping; normal sink calls knowledge ingestion | Reuse scheduling/configuration concepts; introduce a support-case destination before enabling HubSpot sync |
| [Automatic classification](../../klai-portal/backend/app/services/gap_classification.py) | `hard` for no chunks, `soft` for uniformly low available reranker scores or dense scores | Keep as retrieval telemetry, separate from content-gap diagnosis |
| [Human review](../../klai-portal/backend/app/api/app_activity.py) | Maps knowledge-missing/wrong reviews into gaps | Reuse editorial feedback, including explicit rejection of false positives |
| [Gap recording](../../klai-portal/backend/app/services/gap_events.py) | Organization-scoped events, telemetry policy and optional taxonomy classification | Preserve privacy policy; add attributable external case evidence and import idempotency |
| [Gap inbox](../../klai-portal/backend/app/api/app_gaps.py) | Exact question/type/language grouping, frequency, taxonomy and manual resolve | Extend with case links, semantic grouping, diagnosis, and editorial decisions |
| [Gap rescoring](../../klai-portal/backend/app/services/gap_rescorer.py) | Re-runs retrieval after content updates and closes score-based gaps | Do not automatically close content diagnoses solely because retrieval scores improve |
| [Scribe provider](../../klai-scribe/scribe-api/app/services/providers.py) | Sends normalized audio to a self-hosted endpoint with deferred admission and bounded retries; returns text and summary metadata | Reuse inference; preserve segment/speaker evidence separately if the backend provides it |

`record_gap_event` has verified inbound paths from internal events, partner/widget retrieval, and human activity review. Ordinary calls insert events; they do not deduplicate an external import. The current [gap model](../../klai-portal/backend/app/models/retrieval_gaps.py) links widget conversations, not arbitrary HubSpot cases. Do not fabricate widget conversations to fit imported support data.

The connector's normal document path publishes content to the selected knowledge collection. Raw support cases must first remain restricted evidence: otherwise the answer extracted from a case can enter the comparison corpus and hide the gap being investigated. Approved reusable knowledge can enter the existing ingestion pipeline later.

## HubSpot-first implementation sequence

### 1. Establish account access and inspect the real schema

Use the existing app integration where its installation and granted scopes permit it. The [app manifest](../../klai-hubspot/klai-email-support/src/app/app-hsmeta.json) declares `tickets`, `conversations.read` and contact read access; that does not prove a local CLI credential or a particular installed app has those permissions. Keep the first connector read-only in behavior and minimize granted scopes supported by the selected HubSpot app/API version. Do not use a developer personal access key as the production connector credential.

There are three existing authentication paths: the CLI's developer credential; the email card's static app token, injected by HubSpot as `PRIVATE_APP_ACCESS_TOKEN`; and the custom-channel service's OAuth credentials. A working custom-channel installation does not establish ticket access or installation in the intended account. Inspect granted token scopes and account identity before choosing a path. Keep connector credentials scoped to the organization's connector rather than borrowing the custom-channel service's global token.

Read [ticket property metadata](https://developers.hubspot.com/docs/api-reference/legacy/crm/properties/guide), pipeline/stage metadata and available inboxes first. Measure field presence and variation across actual cases rather than requiring speculative custom fields. Do not retain personal field values in discovery reports or public documentation.

| Data group | Initial fields or information to inspect | Purpose |
|---|---|---|
| Ticket | ID, `subject`, `content`, `hs_pipeline`, `hs_pipeline_stage`, creation/update/closure times, owner, priority, custom category/resolution fields | Identity, ordering, workflow and outcome evidence |
| Conversation | Thread ID, ticket association, inbox/channel, status, message timestamps, actor IDs and direction | Reconstruct the actual customer/support exchange |
| Message | ID, text/rich text, type, timestamp, attachments, `truncationStatus` | Cite exact evidence; identify omitted or partial content |
| Notes and CRM activities | Associated note/email/call IDs, bodies, timestamps and recording references where available | Recover internal resolution and off-thread context |
| Knowledge references | URLs or identifiers of articles actually shared, plus current scoped KB content | Compare the support answer with available knowledge |
| Outcome | Customer confirmation, reopen/follow-up signals, explicit resolution notes, otherwise unknown | Distinguish closed workflow from demonstrated success |

The [Conversations API](https://developers.hubspot.com/docs/api-reference/legacy/conversations/guide) exposes threads, ticket associations, paginated messages and an original-content route for truncated messages. [Ticket records](https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/tickets/guide) and [CRM notes](https://developers.hubspot.com/docs/api-reference/legacy/crm/activities/notes/guide) are separate resources. A ticket's `content` property is not its complete conversation. Confirm which activities are actually linked in the account; avoid silently dropping unavailable or unsupported attachments.

### 2. Build a restartable read-only case sync

Add a HubSpot source type to the existing connector configuration/scheduling workflow, with a case-analysis destination. Start with selected pipelines/inboxes and a bounded historical window; fetch subsequent changes on a schedule. Keep raw external data out of the customer-facing KB.

Select tickets server-side with the read-only CRM search endpoint: selected pipelines AND (created within the window OR modified within the window OR still open). Use `hs_lastmodifieddate` for the modification group. Paginate on the fixed API host and reject a scope above HubSpot's 10,000-result search ceiling. This avoids scanning an account's entire ticket history without accepting a truncated snapshot as complete.

Preserve source account/ticket/thread/message identity, source revision, timestamps, content hash, visibility, language and fetch completeness. Record anonymous aggregate completeness counters so field selection can evolve from real use. Missing required pages or failed fetches leave the case incomplete and the run visibly failed/partial.

Handle pagination, rate limits, bounded retries, repeat imports and changed cases. Commit source checkpoints only after corresponding evidence is durably stored. Do not rely solely on ticket modification timestamps to discover changed notes/messages; reconcile the relevant streams and periodically verify their associations. Handle source deletion, access loss and retention expiry without treating a transient fetch failure as a deletion. Add webhooks when polling evidence demonstrates a need; they do not replace reconciliation.

### 3. Analyze cases against the existing knowledge scope

Extract distinct customer questions with applicable product/context, agent reasoning, outcome evidence and exact message/segment references. Retrieve relevant approved knowledge, then assess whether it actually answers each question. A similarity threshold is a candidate filter, not the final diagnosis.

Keep the diagnosis categories above separate from the existing `hard`/`soft` signal. Group semantically related questions while preserving different causes and applicability. Count unique cases and retain cluster examples. Findings need an analysis version and links to evidence and the compared article revisions so a repeat run updates a finding instead of inflating demand.

Reuse the portal gap inbox and taxonomy for editorial work. Extend its source rendering and workflow where required. Confirm visibility against the organization's telemetry policy before persisting case-derived questions. Customer/internal audience boundaries apply to evidence views and drafts as well as published content.

### 4. Reconstruct each conversation medium, then share knowledge comparison

The intended call source is an API delivering raw transcripts. Existing Whisper results are replay inputs for evaluating that path; manual MP3 upload is not the production workflow. When only recordings are available, Scribe remains the transcription component ([contract](scribe-transcription.md)).

**Implementation status:** the reader preserves channel/thread identity and medium, and distinguishes internal notes. The analyzer groups messages by medium and thread, applies call/email/chat preparation with a cautious unknown-medium path, and keeps case-wide context for shared extraction and assessment. Reply references are retained when supplied; HubSpot has no verified per-message reply pointer in this integration. Group-channel and meeting preparation remain future extensions.

Separate three concerns: the **connector** retrieves source records and revisions; the **conversation medium** determines reconstruction and interpretation; the **case** links related exchanges, internal notes and workflow state. A ticket is a case wrapper, not a communication medium. HubSpot's [Conversations API](https://developers.hubspot.com/docs/api-reference/legacy/conversations/guide) exposes channels, threads, messages and ticket associations separately. A call followed by email can belong to one case while keeping both forms of evidence.

Choose preparation per exchange from supplied medium and structure, never solely from vendor or the presence of one transcript somewhere in a case:

| Medium | Preparation before knowledge comparison |
|---|---|
| Calls | Reconstruct topics across turns, interruptions, corrections and transcription errors. Keep timestamps and supplied speaker labels. Distinguish customer needs from agent diagnostic/configuration questions while retaining those questions and answers as context. |
| Email | Reconstruct reply chains using provider relations or message headers. Separate newly authored text, quoted history, forwards and signatures without deleting inline answers or the only available evidence. Preserve sender and recipients. |
| Direct support chat | Reconstruct ordered turns within the conversation, including bot-to-human handoffs, short follow-up messages and reconnects. Preserve actor type; an automated reply is not proof of resolution. |
| Group channels and threads, such as Slack | Retrieve parent messages and replies; separate interleaved conversations outside explicit threads. Preserve participants, mentions, edits and deletions. Uncertain grouping remains explicit rather than assigning nearby messages to a customer case. |
| Meetings | Locate relevant topic spans across multiple speakers; distinguish questions, decisions, proposals and actions. Do not treat every discussion as customer demand. This is a future extension, not an initial connector. |

Internal notes are an evidence kind with an audience boundary, not another medium. A Slack direct support exchange can use direct-chat preparation; a Slack group channel needs thread reconstruction. Internal expert discussions can supply resolution evidence or internal knowledge needs, but do not establish additional customer demand. Retain unknown medium, role and visibility explicitly when the provider cannot establish them. Speaker identity, customer/agent role, human/bot actor type and visibility are separate attributes: [diarization](https://docs.aws.amazon.com/transcribe/latest/dg/diarization.html) supplies speaker labels, not verified business roles.

This separation is an architectural inference from source contracts and research, not a published universal gap-detection standard. [CSDS](https://aclanthology.org/2021.emnlp-main.365/) studies role- and topic-oriented support summaries; [EmailSum](https://aclanthology.org/2021.acl-long.537/) studies email threads; [chat disentanglement](https://aclanthology.org/2023.alta-1.12/) studies intertwined channel conversations; [QMSum](https://aclanthology.org/2021.naacl-main.472/) locates relevant spans in meetings. These support separate reconstruction and evaluation, but do not establish accuracy for this product.

Prefer existing source structure: email [Message-ID, In-Reply-To and References](https://www.rfc-editor.org/rfc/rfc5322.html#section-3.6.4), provider-specific authored content such as Microsoft Graph's [uniqueBody](https://learn.microsoft.com/en-us/graph/api/resources/message?view=graph-rest-1.0), and Slack's [thread_ts and replies](https://docs.slack.dev/messaging/retrieving-messages/). Keep original evidence separate from derived analysis text. Source thread boundaries still need topic interpretation; one thread can contain several needs.

Every preparation produces the same question contract: a standalone customer need grounded in the exchange, applicability, audience, original message/span references, relevant support replies and outcome evidence. This follows the issue/environment/resolution distinction in [KCS article structure](https://library.serviceinnovation.org/KCS/Knowledge-Centered_Success_Practices_Guide/102-Output_the_KCS_Article). A phrase such as "How do I do that?" retains its evidence while becoming a contextualized retrieval query; insufficient context remains unresolved. A support reply is evidence to validate, not automatically authoritative knowledge.

Share case storage, tenant and KB access checks, revision handling, retrieval, answerability assessment, evidence validation, grouping and the gap inbox. Keep source-specific completeness and identity rules in adapters: provider account/call ID and revision for API transcripts; recording hashes for replay inputs. Link cross-channel evidence through established source relationships, not wording alone. Count the same need once per case; receipt of the same revision must not inflate demand, and older revisions must not replace newer evidence. Complete retrieval, a closed ticket and confirmed resolution are distinct states.

Evaluate each medium separately: call roles and contextual questions; email quote duplication and inline answers; chat handoffs; channel interleaving and revisions; mixed cases without duplicate demand. Label expected customer needs, supporting spans and KB coverage before tuning. Test both false proposals and missed needs; fewer extracted questions alone is not improvement. Shared comparison tests should reach the same judgment for equivalent needs presented in different media. Raw conversations remain restricted evidence and never automatically publish knowledge.

Implement the call path and supported HubSpot exchanges first, preserving medium/thread metadata through the payload and analysis. Replay existing transcripts against the configured support KB independently of HubSpot access. Slack and meeting connectors remain deferred until actual source data and access are available; their requirements inform the boundary without adding unused implementations.

### 5. Acceptance gates before continuous operation

- Prove the selected credentials can read actual ticket metadata, linked threads and all required message pages; compare an imported case with the source record.
- A repeat sync yields no duplicate cases/findings; a changed message updates evidence and triggers reanalysis; partial reads are visible and recoverable.
- Test case isolation, evidence access, source deletion and exclusion from ordinary KB retrieval.
- Human-labeled cases distinguish missing, incomplete, already-covered and non-knowledge issues; evaluate missed gaps as well as false proposals.
- Article changes improve held-out answerability; improved retrieval scores alone cannot resolve a content finding.
- Transcription checks confirm nonempty text and, when offered, valid segment timing; transcript quality and speaker attribution require content review.

Deliver in successive slices: preserve source structure and implement call preparation alongside HubSpot access/schema discovery; validate both against the shared evidence-backed analysis; then extend the editorial lifecycle. HubSpot access must not block transcript evaluation. Each implementation slice needs its own agreed scope and regression gates.

## First implementation contract

This contract coordinates the source reader, evidence store, analyzer and portal UI. An installed app's declared scopes are not proof of runtime access. Real-account validation is a release gate; fixtures do not replace it.

### Source and evidence boundary

- Add connector type `hubspot_support`. Reuse existing connector credentials, scheduling, status and deletion. Its KB is the comparison scope, never the destination for raw cases. Only organization-owned KBs are supported initially.
- Configuration: `access_token` (encrypted and masked using the existing credential store), `account_id` (numeric string), `lookback_days` (1–90, default 30), `pipeline_ids` and `inbox_ids` (lists of numeric strings, empty means all). Use Bearer authentication with a private-app token or a [HubSpot service key](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/account-service-keys). Service keys support the scheduled REST reader, not webhooks. OAuth onboarding is a later extension; no new mandatory server environment variables.
- Read metadata, pipelines, tickets, linked threads, all message pages and linked notes/emails. Verify the configured account. Any unavailable required content makes the case incomplete; unsupported attachments are explicit incomplete evidence. Never infer a resolved outcome from a closed stage.
- Reconcile a bounded ticket-creation/modification window on every run, also including older open tickets. A recently modified closed ticket remains eligible even when it was created before the window. Keep the creation and open-stage groups: message/note changes do not necessarily update the ticket timestamp. Older closed cases without a recent ticket modification remain outside coverage; incremental streams and periodic wider reconciliation are the upgrade path.
- A failed or incomplete enumeration must not delete cases or advance a successful checkpoint. Only a fully successful snapshot may reconcile absent IDs. Deletion and retention remove derived findings together with evidence.

### Case payload and storage

`POST /api/internal/connectors/{connector_id}/support-cases` uses the existing portal internal bearer authentication. The server derives organization, owner and KB from the active connector; callers cannot choose another tenant in the body. A payload has these fields:

```text
source: "hubspot" | "audio"
account_id: string
external_id: string
subject: string
language: string | null
source_url: string | null
source_updated_at: ISO timestamp | null
complete: boolean
incomplete_reasons: string[]
messages: [{id: string, kind: "message"|"note"|"email"|"transcript"|"ticket",
            role: "customer"|"agent"|"unknown", text: string,
            occurred_at: ISO timestamp|null, visibility: "customer"|"internal"|"unknown",
            medium: "call"|"email"|"chat"|"unknown" (default "unknown"),
            channel_id: string|null, thread_id: string|null, reply_to_id: string|null,
            speaker_id: string|null,
            start_seconds: number|null, end_seconds: number|null}]
metadata: object (selected workflow fields only)
```

The new message fields default to unknown/null for existing payloads and participate in evidence hashing. `thread_id` identifies the source exchange; `reply_to_id` references a supplied message ID, never an inferred preceding message. HubSpot preserves channel/thread IDs and maps documented channels 1000/1001 to chat and 1002 to email; other channels remain unknown. Whisper replay uses call medium and the recording hash as its thread ID. Speaker IDs remain separate from customer/agent roles. Preparation groups evidence by medium and thread while retaining case-wide context and original IDs; related exchanges are analyzed together to avoid counting a follow-up as a new need.

Deploy the portal's additive payload support before the connector starts sending the new fields: older portal validators reject unknown fields. Reanalysis uses the new analysis version and replaces prior findings within the same case identity.

Use one tenant-scoped `portal_support_cases` table with the validated payload in JSONB, stable identity `(org_id, kb_slug, source, account_id, external_id)`, connector linkage, server-computed content hash, import time, analysis version/results and state (`incomplete`, `pending`, `analyzed`, `failed`). Apply category-D RLS and cascading deletion of derived findings. Do not store credentials or full unfiltered CRM objects in the payload.

Only organizations with `telemetry_level=full` may import or view literal support evidence. Other levels receive an explicit rejection before persistence or model calls. Recheck policy when reading/analyzing; the existing privacy purge must also remove support evidence after a downgrade. No raw case content in application logs.

Case upserts serialize by stable identity. Repeated identical imports do not inflate counts; explicit reanalysis and successful knowledge updates bypass the usual unchanged-evidence shortcut. Changed evidence invalidates old findings; stale analysis must never overwrite a newer revision. Persist evidence before analysis; analysis failure is visible and retryable. The import response is `{case_id, status, changed, findings_count}`. An unsuccessful analysis must not be reported as a successful complete sync.

`POST /api/internal/connectors/{connector_id}/support-cases/reconcile` accepts `{external_ids: string[]}` after a complete successful snapshot and deletes cases no longer in that connector's selected scope. Reconciliation also enforces the configured window; partial snapshots never call it.

### Analyzer interface

Extracted questions retain the language of their supporting request, including
when a case contains multiple languages. The language of the extraction rubric
must not translate the question or change its language group. A new topic does
not inherit an app or device restriction from an earlier question unless the
new request establishes it. This preserves
the requestor's wording for search and grouping, following
[KCS capture guidance](https://library.serviceinnovation.org/KCS/Knowledge-Centered_Success_Practices_Guide/201-Solve_Loop/Practice_3_Capture/Technique_3.2).

`app.services.support_case_analysis.analyze_support_case(*, case: dict, kb_slug: str, zitadel_org_id: str, user_id: str) -> list[dict]` is stateless; storage and API authorization belong to its caller. The module exports `ANALYSIS_VERSION` as a string.

1. Group source exchanges by medium and thread, then extract distinct reusable questions, applicability, audience and source message IDs covering the question, relevant support replies and outcome evidence. Classify each candidate against the complete source before retrieval as `learning_request`, `customer_problem`, `support_work` or `unsupported`. Only the first two continue. A repeatable task requested by support is not evidence that the customer needs instructions for that task. Retained decisions must return a source-faithful standalone question, language and applicability. The corrected question and language replace extraction output in retrieval and assessment; applicability remains diagnostic metadata on the finding. Assessments derive product scope and conditions from the cited source messages, without treating that generated metadata as additional facts. Language corrections use lowercase ISO codes so equivalent language labels cannot split groups. A strict model-response schema requires all decision fields and bounds indexes and source IDs to this request; independent server validation still rejects unknown event types and malformed retained corrections. Use medium-specific preparation while keeping the downstream question contract shared. Pass those actual message texts to the answerability assessment, not only the extracted question. Treat the transcript and retrieved text as untrusted evidence, never as instructions. Unknown speaker roles and unknown outcomes stay unknown.
   Every candidate receives a separate request verification before retrieval: an agent's configuration choices must not become invented customer how-to requests. Retained candidates must cite existing request message IDs; original source text is preserved. Source-event classification does not assign speaker roles. When the exchange establishes a reusable need but call roles remain unknown, the analyzer retains a provisional conversation topic with `audience="unknown"` and an explicit attribution limitation. A call candidate whose evidence establishes neither customer attribution nor an unknown-role request becomes `uncertain` and carries no proposed change. A customer request established by a related email can supply attribution. Malformed verification results fail analysis. Email, chat and internal-note candidates also require an evidenced customer need; only call-backed requests require the additional customer-speaker attribution check. Each model stage has a bounded 120-second timeout to accommodate long transcripts. Source-event classification replaces the previous keep/reject judgment within the same call; it adds no model round trip.
2. Retrieve approved content using the existing retrieval service, explicitly scoped to the selected organization KB and authorized identity.
3. Assess answerability against retrieved passages using the existing configured LiteLLM judge model. The final answer check receives the same cited source messages to resolve terminology and referents; source replies never replace the knowledge passages as answer evidence. Return validated findings with `question`, `language`, `applicability`, `diagnosis`, `rationale`, `missing_information`, `audience`, `message_ids`, `articles`, `gap_type` and `top_score`.

Diagnoses: `missing`, `incomplete`, `outdated`, `contradictory`, `findability`, `audience`, `covered`, `non_knowledge`, `uncertain`. Only the first six create inbox findings. No retrieved passages alone does not establish missing knowledge; the evidence must support the diagnosis. A proposed solution is never automatically authoritative.

Article evidence contains actual returned `chunk_id`, `artifact_id`, `kb_slug`, `source_url`, a server-computed `content_hash` and the compared `text`. Validate message and article references against supplied evidence; reject invented IDs, malformed output and empty justification. Preserve all analyzed outcomes on the case, including covered/non-knowledge/uncertain, so an empty gap list is distinguishable from failed analysis. Support up to 1,000 messages/segments and 200,000 text characters per case; reject larger cases explicitly instead of silently truncating them.

### Existing inbox and transcript input

- Extend `PortalRetrievalGap` with nullable case linkage, diagnosis, stable question key and evidence. Support findings use `gap_type="content"`; retain the analyzer's nullable hard/soft retrieval signal in evidence. This keeps existing hard/soft meanings and callers intact, including when highly relevant articles still have a content gap. One finding per case/question/diagnosis, regardless of import retries. Keep source cases separate from widget conversation IDs.
- Exclude case-backed rows from both the score rescorer's selection and closing UPDATE. Group support findings separately from retrieval telemetry, keeping diagnosis, KB, language and audience distinctions. Bounded semantic matching can assign a verified existing group; measure its accuracy against human-labelled pairs before using frequency as a quality measure.
- Extend the existing gap inbox with source `support`, diagnosis and evidence access. Case detail shows source messages/segments, rationale, missing information and compared articles. Use existing capability/unlock checks, tenant/KB access and explicit loading/error states. Manual close cannot close an unrelated diagnosis or KB group.
- `GET /api/app/gaps/support-cases/{case_id}` returns authorized case evidence and analysis. `POST /api/app/knowledge-bases/{kb_slug}/support-cases/transcript` accepts `{transcript: <native Whisper verbose-JSON result including _source>}`; normalize it to this case contract. Use `_source.sha256` as recording identity; validate finite, ordered segment timing against recording duration (allow up to two seconds of model timestamp overrun) and do not fabricate speakers or dates. Identical recording copies count as one case.
- Add HubSpot setup to the existing connector form and JSON transcript import to the gap workflow. Both use the same backend case analysis; neither publishes knowledge.

### Integration gates

First prove negative cases: raw support cases never call knowledge ingestion; retry and concurrent import do not duplicate findings; changed evidence invalidates prior results; tenant/KB/policy checks deny evidence leakage; high retrieval scores cannot close content findings; partial source pages cannot reconcile deletions. Then validate source pagination, notes, truncation, rate limits, transcript timing/deduplication, model evidence references, grouping and manual close. Run package tests, lint/types, real PostgreSQL RLS/concurrency checks, changed-path mutation tests and the local portal browser flow before the whole-change review.


### Human review workspace

#### Editorial operating contract

Tickets and phone transcripts enter analysis without manual source-content
validation. A support colleague reviews public-chat answer quality in a separate
feedback flow. Knowledge editors review proposed help-center changes, not a queue
of transcripts to label. Existing whole-case reference and speaker-correction
tools are optional diagnostics; the prepared development examples must not become
a required annotation task for support staff.

The target workflow is automatic source analysis, comparison with current scoped
knowledge, a concrete proposal, collaborative editing with Claude, then explicit
editor acceptance and publication through the existing authorized content flow.
Claude assists with drafting and placement; it does not supply human approval.
Each proposal should contain the reusable question, the proposed text change,
an existing article/section or a suggested new location, the reason for that
placement, cited source excerpts, compared articles, and any unresolved facts.
Editors can accept, edit, reject or request clarification. A single explicit
accept-and-publish action can confirm the final text and destination; successful
publication must still be verified before a gap can be considered resolved.

Missing speaker roles do not require staff to annotate the source before useful
drafting can begin. Keep the attribution unknown and any inferred topic or answer
provisional; do not claim a confirmed customer request, generalize account-specific
terms, or invent instructions. A proposal without sufficient answer evidence asks
for the specific missing fact rather than presenting a fabricated ready-to-publish
answer. Source ambiguity and editorial usefulness are separate measurements.

Measure acceptance, material edits, rejection reasons, review time and duplicate
proposals by source channel and overall. These are editorial outcomes, not recall
or proof of published-answer correctness. Reference-set precision/recall remains
available only when independently reviewed case references actually exist.
The current implementation below supports finding review; proposal drafting,
placement, collaborative editing and publication are not yet a complete flow.
Existing Knowledge MCP create/update tools write Klai Docs pages. They do not
establish write access to an externally crawled help center. Resolve the original
publication destination before wiring acceptance to a write; updating a retrieval
copy must not be presented as updating the source help-center article.

The support-case list includes all imported cases, including failed/pending analyses and cases with only covered or uncertain outcomes. It is scoped to one accessible organization knowledge base. Import opens the resulting case even when no gap was created. A case opens on its questions, with cited source excerpts and compared knowledge passages; the original conversation remains available separately.

A reviewer can mark an analysis as correct, incorrect or uncertain and add a note. These are revision-bound judgments; an optional corrected diagnosis controls the derived inbox finding without changing the machine output or publishing knowledge. Source-role corrections and whole-case reference reviews have separate actions. Store them separately from machine analysis in the case's `reviews` JSONB column, keyed by the exact analysis revision and finding index. The revision includes the evidence hash, analysis version and raw analysis output. Reanalysis preserves older review entries without applying them to new output; case deletion and privacy purge delete the annotations with their evidence. There is no separate permanent evaluation archive in this iteration. The additive `reviews` column must exist before the updated endpoints serve traffic; the accompanying owner SQL handles installations where the application migration role does not own the evidence table.

- `GET /api/app/knowledge-bases/{kb_slug}/support-cases?limit=25&offset=0` returns paginated case summaries, including uncertain and reviewed counts.
- Case detail adds `analysis_revision` and each current finding's optional `review`.
- `PATCH /api/app/knowledge-bases/{kb_slug}/support-cases/{case_id}/findings/{finding_index}/review` accepts the expected analysis revision, decision, optional corrected diagnosis and note. Reviewer identity and time come from the server. An outdated analysis returns 409; it is never silently applied to a different question.

The existing capability, feature unlock, organization/KB access and full-evidence telemetry requirements apply to listing, reading and reviewing. Review writes acquire the organization policy lock before the case row lock, matching ingestion order. Concurrent reviews of different findings merge without overwriting one another. No model or external service runs inside that transaction.


## Quality remediation: evidence, review and validation

The ingestion and tenant-isolation tests prove software contracts; they do not
establish detection precision or recall. Release quality must be assessed on
complete conversations, including needs the extractor did not return.

### Comparison and grouping

Use a bounded second retrieval grounded in the case context when the first
search does not establish coverage. Rewrite the question into a concise query
using only the question and relevant case terminology; do not concatenate the
whole exchange. Preserve both queries and the compared
source passages. An answer recovered only on the second search is evidence of
a retrieval/findability problem, not missing content. A missing-content finding
remains a candidate: bounded retrieval cannot prove exhaustive corpus absence.
Failures in either search fail the analysis visibly.

Before accepting `covered` or `findability`, a separate answer check examines
the cited passages for the requested action, product and conditions. Related
links or instructions for a different action are insufficient. A rejected
coverage claim triggers the bounded second search; if still unsupported, the
finding remains uncertain. This model check does not establish measured precision.

Group paraphrases only within the same tenant, comparison KB, language, audience
and diagnosis. A model may select an existing group from a bounded candidate
set; it may not invent a group key. Different devices, product conditions or
procedures must not merge merely because they share a topic. Count distinct
cases, preserve every original question and citation, and retain an explicit
ungrouped outcome. Review grouping against human-labelled pairs before treating
frequency as a reliable measure of demand. Frequency is one prioritization
signal; severity, likely preventability and support effort require observed data.

Grouping judges compatible diagnosis/language/audience cohorts separately and
offers only earlier same-case or existing candidates to each finding. Proposed
matches receive a focused second check against their exact target; only an
unchanged, approved match is retained. All calls share one 120-second deadline.
Invalid responses fail the grouping attempt, preserving the original findings.
Evaluate both missed and false merges on fixed examples in multiple input orders,
including unseen examples with opposite actions and different devices. Model
agreement alone is not an accuracy reference.

The overview groups these distinct needs under existing KB taxonomy topics.
Topic membership never merges separate questions or adds their case counts
together. Topics must belong to the same organization and KB; unclassified
needs remain visible under “Not yet grouped”. Raw automatic retrieval signals
have a separate view because they do not establish missing knowledge.

### Source roles and human corrections

Call speaker IDs and customer/agent roles are separate facts. Accept explicit
roles or a supplied speaker-to-role mapping from the authenticated transcript
source. Unknown roles stay unknown. As an optional diagnostic, an authorized reviewer can correct cited
call-message roles and request reanalysis; record the actor and original source
roles. Do not infer roles from speaker numbering. Raw API transcripts use this
same evidence contract; audio upload is not a required production interface.
Human role overrides are server-owned review data. They survive reimport only
when the message ID, text, kind, medium, timing and speaker still match. Provider
metadata cannot replace that audit. Complete failed or pending cases expose a
revision for retry and role correction; incomplete source cases remain blocked.

Finding verdicts are revision-bound human annotations, separate from machine
output. A corrected diagnosis determines whether a reviewed finding enters the
inbox; a dismissal or uncertain verdict suppresses it. This is editorial
feedback, not automatic model training. A full-case reference review is stored
separately, bound to the evidence content hash. It can contain questions the
model missed, and an explicitly complete empty review means no reusable needs.
Machine output must never be pre-certified as an expert reference.

### Reanalysis and closure

Manual reanalysis and a successful knowledge update must bypass the unchanged
case shortcut. Use the existing update notification and tenant-scoped execution
path. Analysis runs have distinct identities so an older result cannot replace
a newer run even when the source text is unchanged. Do not hold database locks
while retrieving knowledge or calling a model. Preserve tenant feature and
telemetry checks before processing and before applying results.

Recheck content answerability after an article change. Retrieval-score recovery
alone must not close a content gap. Explicit dismissals and historical human
judgments remain auditable; a changed analysis must not silently inherit a
verdict about another finding. Failed analysis remains visible and retryable.

### Evaluation contract

Keep the recordings already used for prompt tuning in a development cohort.
Reserve newly acquired conversations as unseen evaluation cases before tuning;
split at case level, not extracted-question level. Record input hashes, analyzer
version and compared article hashes. No raw customer evidence or credentials
belong in this public repository.

Report source completeness, analysis failures, abstentions, reviewed-case
coverage, false positive findings and missed reference needs separately. Measure
precision and recall only against optional complete human reference reviews. Unreviewed
cases have unavailable quality metrics, never zero error or a perfect score.
An empty prediction set with positive reference gaps has zero recall and
undefined precision. Explain the matching method and independently review
semantic matches rather than presenting model agreement as expert truth.

`klai-portal/backend/scripts/evaluate_support_gaps.py --input cases.json` evaluates exported case-detail records. `--alignment matches.json` supplies explicit human matches between finding and reference indexes, bound to the case content hash and analysis revision. Cases with both predictions and reference questions require this alignment. The report counts the scored subset and unreviewed cases separately; it never guesses matches or emits customer text.

`--grouping --input experiment.json` separately scores grouping within one explicit
tenant/KB snapshot scope. The experiment maps stable reference IDs to predicted
cluster IDs and supplies a complete, independently authored gold partition. It
reports pairwise false and missed merges overall, per channel and cross-channel;
without that gold partition, all grouping quality metrics remain unscored.
`{"scope_id":"kb-v1","reference_kind":"synthetic","reference_source":"fixture-v1",`
`"findings":[{"reference_id":"a","channel":"phone","predicted_cluster_id":"p1"}],`
`"gold_groups":[["a"]]}`. A human-reviewed reference instead uses
`reference_kind: "human_reviewed"` and adds a non-blank `reviewed_by`.

After an approved article edit, replay the affected reference questions and
compare answerability before and after. Operational follow-up measures repeated
support demand and customer resolution. A successful deployment, a large count
of generated gaps, or the disappearance of all inbox rows is not a quality gate.

### Continuous improvement across channels

Prioritize support tickets, calls and internal support questions. Public webchat
is an additional signal, not an equally sized quota for every experiment.
Keep source system, conversation medium, audience and case identity separate:
a call can also appear in a ticket, and an internal question need not represent
customer demand. Link contacts through verified source relationships, not just
similar wording. Scope product, country, language and knowledge base before
combining needs.

Each iteration follows the same sequence:

1. Find a concrete limitation in production data and relevant primary research.
2. Trace the responsible code and record the existing behavior as a baseline.
3. Write a failing behavior test with independently specified expected results.
4. Implement the smallest change and compare on the same inputs. Preserve a
   separate unseen cohort for later model-quality evaluation.
5. Run regression checks and independent review. Publish only when the claimed
   improvement has evidence; verify the deployed version and live behavior.
6. Update this research record with the result, limitations and next question.

The evaluator adds `by_channel` to the existing pooled report. Exported
`payload.source` maps `audio` to `phone` and `hubspot` to `hubspot`. Prepared
evaluation records may explicitly declare `channel` as `phone`, `hubspot`,
`librechat` or `webchat`; conflicting source/channel declarations are rejected.
Absent provenance remains `unknown`. Every observed channel reports processing
status, abstentions, human-reference coverage and its own precision/recall;
unreviewed channels retain null quality metrics. Chat outcome judgments are not
per-question reference answers and cannot be relabelled as such.

The first iteration addresses source coverage before changing model prompts:

- HubSpot includes recently modified older closed tickets, while retaining the
  creation-window and open-stage groups. [CRM search](https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/tickets/search/search-tickets)
  provides the filter boundary; compare real API selections without importing
  the entire expanded cohort. A larger selection is not proof of more true gaps.
- LibreChat excludes judged conversation IDs before the Mongo batch limit.
  Excluding them after the limit can permanently hide older unjudged chats.
  The existing batch size remains bounded. The [Mongo `$nin` documentation](https://www.mongodb.com/docs/manual/reference/operator/query/nin/)
  notes its limited selectivity: measure query cost as the judged set grows
  before replacing it with incremental processing.
- LibreChat marks an assistant failure with a structured `content` error part,
  not only the legacy top-level `error` flag, so the platform-error signal reads
  both. A structured platform error is not itself a knowledge gap: a failure
  after an already-answered turn does not prove the conversation was unresolved,
  so outcome stays an LLM judgment rather than a deterministic label. Feeding a
  large diagnostic error payload to the judge needs its own input and cost
  evaluation, and a longer timeout only proves the turn finished, not that its
  answer was correct.
- Calls with unknown customer attribution remain provisional. Existing tests
  check provisional unknown-role topics and explicit-role evidence. Unknown
  attribution does not suppress an otherwise established conversation topic;
  the finding retains `audience=unknown` and an attribution limitation. Speaker labels
  from [diarization](https://docs.aws.amazon.com/transcribe/latest/dg/diarization.html)
  do not establish customer/agent roles. Obtain provider role metadata or human
  correction before treating those findings as confirmed customer knowledge needs.

Keep operational coverage, detector quality and customer outcomes separate.
Use [offline and online evaluation](https://docs.langchain.com/langsmith/evaluation-types)
for different questions, and calibrate model graders against human judgments
as described in [Anthropic's evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
Research changes the next experiment; it does not authorize automatic knowledge
publication or turn generated labels into expert truth. Private customer inputs,
production counts and experiment artifacts stay outside this public document.

### Checks without human reference labels

Ingest already generates HyPE questions and stores them with the source chunks.
These improve retrieval; generating them does not prove that the detector can
distinguish an answered question from missing knowledge. Reuse that material
for controlled checks rather than introducing another question generator.
[Ragas documents source-derived synthetic test sets](https://docs.ragas.io/en/stable/concepts/test_data_generation/rag/);
their results still need to be distinguished from independent customer-case accuracy.

`scripts/evaluate_ingest_gaps.py` in the portal backend accepts a frozen export
containing `snapshot_id` and enriched `chunks` with their existing `questions`.
It runs the actual support assessor with the source present, then with all
evidence withheld through a per-call retrieval override. Questions the source-present
trial cannot establish are unscorable. Missing, uncertain and incorrect
source-present claims remain separate outcomes. Reports retain hashes and
analyzer provenance without reproducing source text. This measures assessment
after ingestion, not retrieval recall: these questions already participate in
the search index.

The ingest worker also schedules this shared assessment at 02:30 UTC. Internal
portal endpoints discover eligible organization-owned knowledge bases and
recheck tenant policy before assessment: full telemetry and the knowledge-gap
feature must both be enabled. Personal knowledge bases are excluded. The task
reuses enriched source chunks and their existing questions; it does not process
customer conversations during this check.

Each nightly run has a five-minute execution budget and attempts at most ten
questions across eligible scopes. Ten is an operational ceiling, not a sample
size that establishes detector quality. Selection rotates by day, scope and
artifact within a bounded source scan. Reports expose the scan ceiling and
omitted candidates so that this sample cannot be mistaken for full coverage.
Execution and queueing locks prevent overlapping runs and duplicate waiting
jobs; [Procrastinate documents these as separate lock guarantees](https://procrastinate.readthedocs.io/en/stable/howto/advanced/locks.html).

Existing evaluation rows store source identifiers, hashes, analyzer versions
and outcomes without source text. Precision and recall remain null. A nightly
sample remains inconclusive about overall detector quality even when all scored
pairs pass. Unscorable questions remain visible, and external-service failures
fail the task after persisting available results. Larger fixed customer-case
comparisons and independently reviewed references remain separate evaluations.

`scripts/evaluate_support_gaps.py --repeat-input second.json --kb-snapshot ID
--repeat-kb-snapshot ID --input first.json` compares repeated case analyses.
Both exports must describe the same case content and explicit KB snapshot.
Keep analyzer and model versions fixed when measuring run-to-run variation.
Question matching is wording-sensitive; changed wording is reported as an
unmatched old and new finding, not as a proven semantic change. Human-reference
precision and recall remain a separate mode.

The ingest service's existing scheduled RAGAS suites measure retrieval and
answer quality separately. Retrieval failures persist diagnostic rows but fail
the overall run, including partially completed evaluations; a completed worker
task must not stand in for a valid quality measurement.
