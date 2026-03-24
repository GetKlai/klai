# SPEC-GRAFANA-METRICS: Research

## Codebase Analysis

### Platform Features

Klai is a Dutch B2B SaaS AI platform with three main features:

1. **Chat** — AI-powered chat with knowledge base (powered by LibreChat per-tenant containers, Qdrant for vector search, LiteLLM for model routing)
2. **Scribe** — AI meeting transcription and summarization (powered by Vexa bot manager, Whisper, LiteLLM summarization)
3. **Focus** — AI research notebooks with sources (powered by a separate `/research/v1` service, notebook-based with personal/org scope)

### Subscription Tiers

From `billing.tsx` and `portal.py`:

| Plan ID        | Features                      | Monthly | Early adopter yearly | Regular yearly |
|----------------|-------------------------------|---------|----------------------|----------------|
| `free`         | Limited/trial access          | 0       | —                    | —              |
| `core`         | Chat + Focus                  | €28     | €20/seat             | €22/seat       |
| `professional` | Chat + Focus + Scribe         | €42     | €29/seat             | €34/seat       |
| `knowledge`    | Chat + Focus + Scribe + KB    | €68     | €48/seat             | €54/seat       |

Billing cycles: `monthly` | `yearly_early` | `yearly`
Note: early adopter pricing is a launch-phase discount for early customers.
Billing statuses: `pending` | `mandate_requested` | `active` | `payment_failed` | `cancelled`
Billing provider: Moneybird (Dutch invoicing platform)

### Data Model

**PortalOrg** (`portal_orgs`):
- `id`, `zitadel_org_id`, `name`, `slug`
- `plan` (core/professional/complete/free), `billing_cycle` (monthly/yearly), `seats`
- `billing_status` (pending/mandate_requested/active/payment_failed/cancelled)
- `moneybird_contact_id`, `moneybird_subscription_id`
- `provisioning_status`, `mfa_policy`, `default_language`
- `librechat_container`, `litellm_team_key`
- `created_at`

**PortalUser** (`portal_users`):
- `id`, `zitadel_user_id`, `org_id` (FK), `role` (admin/member)
- `preferred_language`, `created_at`

**VexaMeeting** (`vexa_meetings`):
- `id` (UUID), `zitadel_user_id`, `org_id` (FK)
- `platform` (google_meet/zoom/teams), `native_meeting_id`, `meeting_url`, `meeting_title`
- `bot_id`, `vexa_meeting_id`, `status` (pending/joining/recording/processing/done/failed)
- `transcript_text`, `transcript_segments` (JSONB), `summary_json` (JSONB)
- `language`, `duration_seconds`, `error_message`
- `started_at`, `ended_at`, `created_at`, `updated_at`

### Authentication

- Zitadel (self-hosted, EU) for identity management
- OIDC-based auth with custom login UI
- SSO cookie mechanism for cross-subdomain auth (LibreChat integration)
- Roles: `admin` (org creator), `member`

### Existing Infrastructure

- `klai-core-grafana-1` — Grafana instance on core-01
- `klai-core-postgres-1` — Portal PostgreSQL on core-01
- `klai-core-victoriametrics-1` — VictoriaMetrics on core-01

### What Is NOT in Postgres Today

Chat usage is handled by LibreChat (per-tenant containers with MongoDB) and LiteLLM (separate service with its own database). Token usage and chat message counts are tracked by LiteLLM, not the portal backend.

Focus notebooks are served by a separate `/research/v1` service -- not the portal backend Postgres.

Knowledge base chunks are in Qdrant (vector database), with metadata like `org_id`, `kb_slug`, `user_id`.

---

## Market Standards: SaaS Product & Business Metrics

### Industry Benchmarks (Amplitude, Mixpanel, Intercom, ChartMogul)

**Business Metrics (ChartMogul / Baremetrics pattern):**
- MRR (Monthly Recurring Revenue) — the single most important SaaS metric
- ARR (Annual Recurring Revenue) = MRR x 12
- Net Revenue Retention (NRR) — expansion vs contraction
- Customer count by plan tier
- Churn rate (logo churn and revenue churn)
- Trial-to-paid conversion rate
- ARPU (Average Revenue Per User/Account)
- LTV (Lifetime Value) — requires sufficient history

**Product Metrics (Amplitude / Mixpanel pattern):**
- DAU / WAU / MAU — daily/weekly/monthly active users
- DAU/MAU ratio ("stickiness") — healthy B2B SaaS: 15-25%
- Feature adoption rate — % of users using each feature
- Activation rate — % of signups completing key actions within first 7 days
- Time to first value — how quickly new users experience core value
- Retention cohorts — week-over-week or month-over-month

**Feature-Specific Metrics (for Klai):**
- Chat: messages sent per user/org, conversations started, knowledge base queries
- Scribe: meetings recorded, transcription minutes, summaries generated
- Focus: notebooks created, sources added, research sessions

**Health Metrics (Datadog / Grafana Cloud pattern):**
- API error rate per endpoint
- API latency (P50, P95, P99) per endpoint
- Signup success/failure rate
- Provisioning success/failure rate

### What Actually Drives Decisions at Early Stage

For an early-stage B2B SaaS, the pragmatic top 10-15 metrics are:

**P0 — Must have (business survival):**
1. Total customers by plan tier (are we growing?)
2. Total seats sold (revenue proxy)
3. Billing status distribution (active vs cancelled vs pending)
4. New signups over time (growth rate)
5. Churn: cancellations over time

**P1 — Should have (product-market fit signals):**
6. DAU/WAU (are customers actually using it?)
7. Feature adoption: which features are being used, by what % of orgs
8. Scribe usage: meetings per org, transcription minutes
9. Provisioning health: success/failure rate, time to provision
10. API error rate (system health)

**P2 — Nice to have (optimization):**
11. Activation funnel: signup -> email verified -> first chat / first meeting
12. Retention cohorts by signup month
13. Chat usage patterns (via LiteLLM if accessible)
14. Focus notebook creation and usage patterns
15. Knowledge base growth (Qdrant chunk counts)

### Pragmatic Approach: Events Table

Rather than instrumenting every service, the simplest approach for Klai:

1. **Direct Postgres queries** for business metrics (plan distribution, signups, billing status) -- data already exists in `portal_orgs` and `portal_users`
2. **Events table** for product metrics that need explicit tracking (feature usage, user actions)
3. **LiteLLM database** for chat/token usage (if Grafana can connect to it)
4. **VexaMeeting table** for Scribe metrics (data already exists)

This avoids building a full event pipeline and leverages existing data.
