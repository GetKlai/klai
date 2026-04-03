# Domain Model

> Last updated: 2026-04-03

## Portal Core Entities (klai-portal/backend/app/models/)

### PortalOrg (portal_orgs) — portal.py
- `id`: int PK
- `zitadel_org_id`: str(64) unique, indexed — links to Zitadel organisation
- `name`: str(255)
- `slug`: str(64) unique, indexed — URL-safe org identifier
- `billing_status`: text, default "pending" — pending | active | suspended | cancelled
- `plan`: text, default "professional" — core | professional | complete
- `billing_cycle`: text, default "monthly" — monthly | yearly
- `seats`: int, default 1
- `default_language`: str(8), default "nl" — nl | en
- `provisioning_status`: str(32), default "pending" — pending | provisioning | provisioned | failed
- `mfa_policy`: str(16), default "optional" — optional | recommended | required
- `librechat_container`: str(128), nullable — Docker container name for this tenant
- `zitadel_librechat_client_id`: str(128), nullable — OIDC app per tenant
- `zitadel_librechat_client_secret`: LargeBinary, nullable — encrypted OIDC secret
- `litellm_team_key`: LargeBinary, nullable — encrypted LiteLLM key
- `moneybird_contact_id`: text, nullable — billing reference
- `moneybird_subscription_id`: text, nullable — billing reference
- `mcp_servers`: JSON, nullable — per-org MCP server configuration
- `created_at`: datetime(tz), server_default now()
- **Relationships:** `users` → PortalUser (back_populates "org")

### PortalUser (portal_users) — portal.py
- `id`: int PK
- `zitadel_user_id`: str(64) unique, indexed — links to Zitadel user
- `org_id`: int FK → portal_orgs.id
- `role`: str(20), default "member" — admin | group-admin | member
- `status`: str(16), default "active" — active | suspended | offboarded (CHECK constraint)
- `preferred_language`: str(8), default "nl" — nl | en
- `github_username`: str(128), nullable
- `display_name`: str(255), nullable — cached display name
- `email`: str(255), nullable — cached email
- `librechat_user_id`: str(64), nullable, indexed — cached LibreChat MongoDB ObjectId
- `kb_retrieval_enabled`: bool, default true — KB scope preference
- `kb_personal_enabled`: bool, default true — personal KB toggle
- `kb_slugs_filter`: ARRAY(str(128)), nullable — selected KB slugs
- `kb_narrow`: bool, default false — narrow retrieval mode
- `kb_pref_version`: int, default 0 — cache discriminator for LiteLLM hook
- `created_at`: datetime(tz), server_default now()
- **Relationships:** `org` → PortalOrg (back_populates "users")

### VexaMeeting (vexa_meetings) — meetings.py
- `id`: UUID PK, default uuid4
- `zitadel_user_id`: str(64), indexed — owner
- `org_id`: int FK → portal_orgs.id, nullable
- `group_id`: int FK → portal_groups.id (ondelete SET NULL), nullable, indexed
- `platform`: str(32) — google_meet | zoom | teams
- `native_meeting_id`: str(128)
- `meeting_url`: text
- `meeting_title`: str(255), nullable
- `bot_id`: str(128), nullable — Vexa bot reference
- `vexa_meeting_id`: int, nullable — Vexa platform meeting ID
- `status`: str(32), default "pending" — pending | joining | active | recording | processing | done | failed
- `consent_given`: bool, default false (GDPR)
- `transcript_text`: text, nullable
- `transcript_segments`: JSONB, nullable — structured segments
- `summary_json`: JSONB, nullable — AI-generated summary
- `language`: str(16), nullable
- `duration_seconds`: int, nullable
- `error_message`: text, nullable
- `ical_uid`: str(512), nullable, unique, indexed — calendar integration
- `recording_deleted`: bool, default false
- `recording_deleted_at`: datetime(tz), nullable
- `started_at`: datetime(tz), nullable
- `ended_at`: datetime(tz), nullable
- `created_at`: datetime(tz), server_default now()
- `updated_at`: datetime(tz), server_default now(), onupdate now()

## Knowledge Base Entities — knowledge_bases.py

### PortalKnowledgeBase (portal_knowledge_bases)
- `id`: int PK
- `org_id`: int FK → portal_orgs.id
- `name`: str(128)
- `slug`: str(64) — unique per org (composite unique: org_id + slug)
- `description`: text, nullable
- `visibility`: text, default "internal"
- `docs_enabled`: bool, default true
- `gitea_repo_slug`: text, nullable
- `owner_type`: text, default "org" — org | personal
- `owner_user_id`: text, nullable — set when owner_type=personal
- `created_at`: datetime(tz), server_default now()
- `created_by`: str(64)

### PortalUserKBAccess (portal_user_kb_access)
- `id`: int PK
- `kb_id`: int FK → portal_knowledge_bases.id (CASCADE)
- `user_id`: text — Zitadel user ID
- `org_id`: int FK → portal_orgs.id (CASCADE)
- `role`: text — access role
- `granted_at`: datetime(tz), server_default now()
- `granted_by`: text

### PortalGroupKBAccess (portal_group_kb_access)
- `id`: int PK
- `group_id`: int FK → portal_groups.id (CASCADE)
- `kb_id`: int FK → portal_knowledge_bases.id (CASCADE)
- `role`: text, default "viewer"
- `granted_at`: datetime(tz), server_default now()
- `granted_by`: str(64)

### PortalKBTombstone (portal_kb_tombstones)
- `id`: int PK
- `org_id`: int FK → portal_orgs.id
- `slug`: str(64) — unique per org
- `deleted_at`: datetime(tz), server_default now()
- `deleted_by`: str

## Group & Product Entities — groups.py, products.py

### PortalGroup (portal_groups)
- `id`: int PK
- `org_id`: int FK → portal_orgs.id
- `name`: str(128)
- `description`: text, nullable
- `is_system`: bool, default false — system-managed group
- `system_key`: str(32), nullable — identifier for system groups
- `created_at`: datetime(tz), server_default now()
- `created_by`: str(64)

### PortalGroupMembership (portal_group_memberships)
- `id`: int PK
- `group_id`: int FK → portal_groups.id (CASCADE)
- `zitadel_user_id`: str(64)
- `is_group_admin`: bool, default false
- `joined_at`: datetime(tz), server_default now()
- Unique: (group_id, zitadel_user_id)

### PortalGroupProduct (portal_group_products)
- `id`: int PK
- `group_id`: int FK → portal_groups.id (CASCADE)
- `org_id`: int FK → portal_orgs.id
- `product`: str(32)
- `enabled_at`: datetime(tz), server_default now()
- `enabled_by`: str(64)
- Unique: (group_id, product)

### PortalUserProduct (portal_user_products)
- `id`: int PK
- `zitadel_user_id`: str(64)
- `org_id`: int FK → portal_orgs.id
- `product`: str(32)
- `enabled_at`: datetime(tz), server_default now()
- `enabled_by`: str(64)
- Unique: (zitadel_user_id, product)

## Taxonomy Entities — taxonomy.py

### PortalTaxonomyNode (portal_taxonomy_nodes)
- `id`: int PK
- `kb_id`: int FK → portal_knowledge_bases.id (CASCADE)
- `parent_id`: int FK → portal_taxonomy_nodes.id (SET NULL), nullable — self-referencing tree
- `name`: str(128)
- `slug`: str(128)
- `doc_count`: int, default 0
- `sort_order`: int, default 0
- `created_at`: datetime(tz), server_default now()
- `created_by`: str(64)
- Unique: (kb_id, parent_id, name) when parent_id IS NOT NULL
- Unique: (kb_id, name) when parent_id IS NULL (root nodes)

### PortalTaxonomyProposal (portal_taxonomy_proposals)
- `id`: int PK
- `kb_id`: int FK → portal_knowledge_bases.id (CASCADE)
- `proposal_type`: str(32) — new_node | merge | split | rename (CHECK)
- `status`: str(32), default "pending" — pending | approved | rejected (CHECK)
- `title`: str(256)
- `payload`: JSONB
- `confidence_score`: float, nullable
- `created_at`: datetime(tz), server_default now()
- `reviewed_at`: datetime(tz), nullable
- `reviewed_by`: str(64), nullable
- `rejection_reason`: text, nullable

## Connector Entities — connectors.py

### PortalConnector (portal_connectors)
- `id`: str (UUID, server_default gen_random_uuid()) PK
- `kb_id`: int FK → portal_knowledge_bases.id (CASCADE)
- `org_id`: int FK → portal_orgs.id (CASCADE)
- `name`: text
- `connector_type`: text
- `config`: JSONB, default {}
- `schedule`: text, nullable
- `is_enabled`: bool, default true
- `last_sync_at`: datetime(tz), nullable
- `last_sync_status`: text, nullable
- `content_type`: text, nullable
- `allowed_assertion_modes`: JSONB, nullable
- `created_at`: datetime(tz), server_default now()
- `created_by`: text

## Audit & Analytics Entities — audit.py, events.py

### PortalAuditLog (portal_audit_log)
- `id`: int PK autoincrement
- `org_id`: int (no FK — RLS-protected, independent session writes)
- `actor_user_id`: str(64)
- `action`: str(64)
- `resource_type`: str(32)
- `resource_id`: str(128)
- `details`: JSONB, nullable
- `created_at`: datetime(tz), server_default now()
- Index: (org_id, created_at) for paginated queries
- **RLS:** Split SELECT/INSERT policies; uses independent session + raw SQL (no ORM RETURNING)

### ProductEvent (product_events)
- `id`: BigInteger PK autoincrement
- `event_type`: str(64), indexed
- `org_id`: int FK → portal_orgs.id (SET NULL), nullable
- `user_id`: str(64), nullable
- `properties`: JSONB, default {}
- `created_at`: datetime(tz), server_default now()

## Retrieval Gap Tracking — retrieval_gaps.py

### PortalRetrievalGap (portal_retrieval_gaps)
- `id`: int PK
- `org_id`: int FK → portal_orgs.id (CASCADE)
- `user_id`: str
- `query_text`: str
- `gap_type`: str — hard | soft (CHECK)
- `top_score`: float (Double), nullable
- `nearest_kb_slug`: str, nullable
- `chunks_retrieved`: int, default 0
- `retrieval_ms`: int, default 0
- `occurred_at`: datetime(tz), server_default now()
- `resolved_at`: datetime(tz), nullable
- Indexes: (org_id, occurred_at), (org_id, query_text), partial index on open gaps

## Key Business Rules
- Each org gets its own LibreChat container + Zitadel OIDC app on provisioning
- LiteLLM team key scopes AI usage per org
- Moneybird billing is NL/EU only — never suggest non-EU billing alternatives
- Seats tracked on PortalOrg for billing calculation
- Vexa `completed` webhook is the primary trigger for run_transcription; bot_poller is fallback only
- Tenant secrets (librechat_client_secret, litellm_team_key) stored as encrypted bytes (LargeBinary)
- PortalAuditLog uses independent DB session + raw SQL to survive caller exceptions and bypass RLS RETURNING issues
- KB access is layered: org scope → user/group KB access → personal ownership check
- Taxonomy is a self-referencing tree per knowledge base with AI-generated proposals