# Tenant Deprovisioning Runbook

> Source of truth: `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py`.
> This runbook is operational guidance around the live delete path, not a parallel manual delete spec.

Tenant deprovisioning is a one-way hard delete of a workspace. The portal orchestrator removes tenant-owned resources from external systems first, then hard-deletes the `portal_orgs` row. The surviving audit record in `tenant_lifecycle_events` is intentional.

The delete contract is **fail loudly**:

- Missing required service URLs or secrets fail the run.
- Missing internal wipe endpoints fail the run.
- External lookup failures fail the run.
- Already-absent resources are still idempotent when the target service can confirm absence.

## Entry Points

Owner self-service:

```bash
DELETE /api/admin/org/me
```

Platform admin:

```bash
curl -X DELETE "https://my.getklai.com/api/admin/orgs/{slug}/deprovision" \
  -H "Authorization: Bearer $PLATFORM_ADMIN_TOKEN"
```

Expected response:

```json
{"status": "queued", "org_slug": "{slug}"}
```

The endpoint transitions the org to `deprovisioning`, invalidates the slug cache, returns `202 Accepted`, and starts the background orchestrator.

## Live Step Order

Current orchestrator: **21 steps**.

| # | Step | Resource |
|---|---|---|
| 0 | `_mark_deprovisioning` | Portal state + slug cache |
| 1 | `_delete_caddy_upstream` | Caddy tenant route |
| 2 | `_delete_librechat_container` | Docker container |
| 3 | `_delete_librechat_filesystem` | LibreChat tenant files |
| 4 | `_drop_mongodb_database` | LibreChat MongoDB database |
| 5 | `_drop_mongodb_user` | LibreChat MongoDB user |
| 6 | `_delete_meilisearch_index` | Meilisearch index |
| 7 | `_flush_redis_tenant_keys` | `configs:{slug}:*` Redis keys |
| 8 | `_delete_qdrant_points` | `klai_knowledge` points by Zitadel org id |
| 9 | `_delete_falkordb_graph` | Knowledge-ingest graph wipe |
| 10 | `_wipe_knowledge_postgres` | `knowledge.*` Postgres rows |
| 11 | `_wipe_klai_connector_state` | `connector.sync_runs` + `connector.connectors` (incl. encrypted creds) |
| 12 | `_wipe_scribe_state` | Scribe DB rows + retained local audio |
| 13 | `_delete_scribe_artifacts` | Scribe Garage/S3 prefix |
| 14 | `_delete_litellm_team` | LiteLLM team |
| 15 | `_archive_moneybird_subscription` | Moneybird subscription/contact |
| 16 | `_delete_personal_kb` | Docs personal KB |
| 17 | `_delete_zitadel_oidc_app` | Zitadel OIDC app |
| 18 | `_delete_zitadel_users` | Single-tenant Zitadel users |
| 19 | `_delete_zitadel_org` | Zitadel tenant org |
| 20 | `_finalize_postgres_delete` | Portal audit event + portal hard delete |

Important identifier rule:

- `portal_orgs.id` is the portal integer org id.
- `portal_orgs.zitadel_org_id` is the external tenant id.
- Qdrant, FalkorDB, knowledge-ingest, connector, and Scribe wipes use the **Zitadel org id**, not the portal integer id.

## Monitoring

VictoriaLogs query:

```text
service:portal-api AND (deprovisioning OR wipe OR deleted) AND slug:{slug}
```

Expected high-level events:

```text
deprovisioning_started
... step-specific success events ...
deprovisioning_complete
```

Failure event:

```text
deprovisioning_failed step=<step_name> error=<truncated error> org_id=<portal_org_id>
deprovisioning_failed_state_set step=<step_name>
```

The status endpoint intentionally sanitizes `last_failure` for tenant admins; platform logs are the full diagnostic source.

## Retry

If the org is in `failed_deprovisioning`, retry from the platform admin endpoint:

```bash
curl -X POST "https://my.getklai.com/api/admin/orgs/{slug}/retry-deprovisioning" \
  -H "Authorization: Bearer $PLATFORM_ADMIN_TOKEN"
```

The orchestrator restarts from the beginning. Steps are idempotent, so already-deleted resources should be harmless. Do not manually delete portal rows before retrying; the live orchestrator needs the org row as source of truth for ids.

## When To Investigate Before Retrying

| Failed step | Check |
|---|---|
| `_delete_falkordb_graph` / `_wipe_knowledge_postgres` | `knowledge_ingest_url`, `knowledge_ingest_secret`, and knowledge-ingest route availability |
| `_wipe_klai_connector_state` | `klai_connector_url`, `klai_connector_secret`, connector internal route availability |
| `_wipe_scribe_state` | `scribe_api_url`, `internal_secret`/`PORTAL_INTERNAL_SECRET`, Scribe internal route availability |
| `_delete_scribe_artifacts` | `garage_s3_endpoint`, Garage credentials, bucket availability |
| `_delete_litellm_team` | LiteLLM `/team/list` and `/team/delete` availability |
| `_archive_moneybird_subscription` | Moneybird token and Moneybird API availability |
| `_delete_zitadel_oidc_app` / `_delete_zitadel_org` / `_delete_zitadel_users` | Zitadel PAT, project id, management API availability |
| `_finalize_postgres_delete` | New non-cascading FK to `portal_orgs.id`, or legacy table drift |

## Post-Delete Verification

Use these checks after `deprovisioning_complete` or after a successful retry.

Portal:

```sql
SELECT COUNT(*) FROM portal_orgs WHERE slug = '{slug}';
SELECT COUNT(*) FROM tenant_lifecycle_events WHERE org_slug_snapshot = '{slug}' AND event_type = 'deprovisioned';
```

Knowledge-ingest:

```bash
curl -X POST "http://knowledge-ingest:8000/internal/v1/orgs/{zitadel_org_id}/wipe-postgres" \
  -H "X-Internal-Secret: $KNOWLEDGE_INGEST_SECRET"
```

Expected after successful delete: all returned `rows_deleted` counts are `0`.

Connector:

```bash
curl -X POST "http://klai-connector:8200/internal/v1/orgs/{zitadel_org_id}/wipe-state" \
  -H "Authorization: Bearer $KLAI_CONNECTOR_SECRET"
```

The endpoint deletes BOTH `connector.sync_runs` and `connector.connectors`
(the latter holds the tenant's encrypted OAuth/API credentials, so this is
the GDPR-critical purge). `rows_deleted` is the total across both; `per_table`
breaks it out. Expected after successful delete: `rows_deleted` is `0`.

Scribe:

```bash
curl -X POST "http://scribe-api:8020/internal/v1/orgs/{zitadel_org_id}/wipe-state" \
  -H "X-Internal-Secret: $PORTAL_API_INTERNAL_SECRET"
```

Expected after successful delete: `rows_deleted` and `audio_files_deleted` are `0`.

Qdrant:

```bash
curl -X POST "http://qdrant:6333/collections/klai_knowledge/points/scroll" \
  -H "Content-Type: application/json" \
  -d '{"limit":1,"filter":{"must":[{"key":"org_id","match":{"value":"{zitadel_org_id}"}}]}}'
```

Expected after successful delete: no points.

## Manual Cleanup Policy

Manual cleanup is last resort only. Prefer fixing the failing service/config and using retry, because the orchestrator needs `portal_orgs` data until the final step.

If manual cleanup is unavoidable:

1. Record the portal integer id, slug, org name, Zitadel org id, LiteLLM team id, Moneybird ids, and Zitadel OIDC app id first.
2. Remove only the resource for the failing step.
3. Retry the orchestrator.
4. Do not manually delete `portal_orgs` unless all external resources have been verified clean and you are deliberately taking over the final audit/hard-delete step.

## Portal Final Delete Coverage

The final step emits a `tenant_lifecycle_events` audit row, then deletes non-cascading children before `portal_orgs`:

```text
portal_knowledge_bases
portal_docs_libraries
portal_kb_tombstones
vexa_meetings
portal_group_products
portal_groups
portal_templates
portal_user_products
portal_user_seat_history
portal_users
portal_join_requests
portal_orgs
```

If `_finalize_postgres_delete` fails with an FK violation, audit the current schema for a new non-cascading FK to `portal_orgs.id` and update the live step plus tests. Do not add ad hoc SQL only to this runbook.

## GDPR Notes

Normal tenant delete removes workspace data from operational systems. These records intentionally survive:

- `tenant_lifecycle_events` deletion evidence.
- Moneybird financial records required for statutory retention.
- Logs until normal VictoriaLogs retention expires.

Only purge lifecycle audit rows on explicit legal approval:

```sql
DELETE FROM tenant_lifecycle_events WHERE org_slug_snapshot = '{slug}';
```

## References

- Portal endpoints: `klai-portal/backend/app/api/admin/deprovision_org.py`
- Orchestrator: `klai-portal/backend/app/services/provisioning/deprovisioning_orchestrator.py`
- Steps: `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py`
- Scribe internal wipe: `klai-scribe/scribe-api/app/api/internal.py`
- Knowledge internal wipes: `klai-knowledge-ingest/knowledge_ingest/routes/internal.py`
- Connector internal wipe: `klai-connector/app/routes/internal.py`
