# Domain Model

## Portal Core Entities (klai-portal/backend/app/models/)

### PortalOrg (portal_orgs)
- `zitadel_org_id` — links to Zitadel organisation
- `slug` — URL-safe org identifier (unique)
- `billing_status`: pending | active | suspended | cancelled
- `plan`: core | professional | complete
- `billing_cycle`: monthly | yearly
- `seats`: int
- `default_language`: nl | en
- `provisioning_status`: pending | provisioning | provisioned | failed
- `mfa_policy`: optional | recommended | required
- `librechat_container` — Docker container name for this tenant
- `zitadel_librechat_client_id/secret` — OIDC app per tenant
- `litellm_team_key` — LiteLLM key for this org's usage
- `moneybird_contact_id/subscription_id` — billing references

### PortalUser (portal_users)
- `zitadel_user_id` — links to Zitadel user
- `org_id` → PortalOrg
- `role`: admin | member
- `preferred_language`: nl | en
- **Mapping-only:** no email/name stored — always fetched live from Zitadel

### VexaMeeting (vexa_meetings)
- `zitadel_user_id` — owner
- `org_id` → PortalOrg
- `platform`: google_meet | zoom | teams
- `status`: pending | joining | active | recording | processing | done | failed
  - `pending` — meeting created, bot not yet dispatched
  - `joining` — bot dispatched, waiting to enter meeting
  - `active` — bot in meeting, recording
  - `recording` — everyoneLeft timeout counting down (≤5s)
  - `processing` — stop called, waiting for Vexa webhook
  - `done` — transcription complete
  - `failed` — error in transcription or bot
- `consent_given`: bool (GDPR)
- `transcript_text/segments` — stored post-meeting
- `bot_id` — Vexa bot reference

## Connector Entities (klai-connector)
Manages OAuth credentials for connectors (GitHub etc.) with AES-GCM encryption at rest via PostgresSecretsStore.

## Key Business Rules
- Each org gets its own LibreChat container + Zitadel OIDC app on provisioning
- LiteLLM team key scopes AI usage per org
- Moneybird billing is NL/EU only — never suggest non-EU billing alternatives
- Seats tracked on PortalOrg for billing calculation
- Vexa `completed` webhook is the primary trigger for run_transcription; bot_poller is fallback only
