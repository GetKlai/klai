# Runbook: login-wall burst alert

**Alert**: `login_wall_burst`
**Severity**: warning
**Spec**: [SPEC-INGEST-LOGIN-WALL-DETECT-001](../../.moai/specs/SPEC-INGEST-LOGIN-WALL-DETECT-001/spec.md)

## What this means

knowledge-ingest's anonymous-crawl auth-wall detector emitted more than 50
detections in the last 10 minutes. The detector flags pages whose markdown
matches one of:

- canonical login phrase ("you will have to log in with your X account",
  "log in to read", etc.)
- 5+ `redirect_to=` URL parameters
- 5+ repetitions of the same `/login` href
- short content + 3+ login anchors

A sustained burst means **the system is currently catching a lot of walled
pages**. That is good behaviour; this alert is meant to flag *unexpected*
volumes so an operator can decide whether they're expected (backfill running,
new tenant onboarding) or a regression (vendor changed auth, connector
mis-configured).

## Triage

### Step 1 — Are we mid-backfill?

```bash
# VictoriaLogs query (paste in Grafana → Explore → victorialogs):
service:knowledge-ingest AND event:backfill_login_walls_complete _time:[now-1h, now]
```

If a `backfill_login_walls_complete` event fired within the last hour AND
the burst window matches the backfill timing → **expected**, no action.
Wait for the rate to drop after the backfill finishes.

### Step 2 — Which tenant is dominant?

```
service:knowledge-ingest AND (event:login_wall_reject OR event:login_wall_degrade OR event:login_wall_detected)
| stats by(org_id, kb_slug) count()
| sort desc
| limit 5
```

If a single `org_id` accounts for > 80% of detections:

- Check if it's a newly-onboarded tenant (created in the last 24h):
  ```sql
  SELECT id, slug, created_at FROM portal_orgs
  WHERE zitadel_org_id = '<org_id_from_log>';
  ```
- New tenant + high rate = their first crawl is hitting login-walls. Likely
  causes:
  1. **Source site requires login** but their connector was set up without
     cookies. Configure cookies via the existing connector wizard
     ([SPEC-CRAWLER-004](../../.moai/specs/SPEC-CRAWLER-004/spec.md) cookie
     path) and re-crawl.
  2. **Stale crawl that already-walled pages were re-fetched**. Run the
     backfill CLI to clean them out:
     ```bash
     ssh core-01 "docker exec klai-core-knowledge-ingest-1 \
       python -m knowledge_ingest.backfill_tasks --org <slug> --kb <slug>"
     ```

### Step 3 — Did a vendor change their auth model?

If the burst comes from a tenant that has been on the system for weeks +
their existing connector hasn't been touched → the upstream vendor likely
gated previously-public content.

- Identify the page domain dominating the detections:
  ```
  service:knowledge-ingest AND event:login_wall_reject
  | extract regex 'url=(?P<domain>https?://[^/ ]+)' from _msg
  | stats by(domain) count() | sort desc
  ```
- Decide:
  - Configure cookies + re-crawl (preferred if the content is essential).
  - Accept the loss + run backfill to remove the walled chunks from Qdrant
    so retrieval doesn't surface them.
  - Reach out to the customer to discuss alternatives (manual upload,
    different source, etc.).

## False positives

If a single tenant insists their pages are NOT login-walled:

1. Pull the actual stored markdown:
   ```sql
   SELECT raw_markdown FROM knowledge.crawled_pages
   WHERE org_id = '<org>' AND url = '<exact-url>';
   ```
2. Read the markdown. The detector logs the matching pattern:
   `pattern=canonical_phrase_en` etc.
3. If the pattern looks like a false positive, capture the markdown to
   `klai-knowledge-ingest/tests/fixtures/clean_pages/<source>.md` as a
   negative-case fixture and open a follow-up SPEC to refine the detector.

## Resolution

The alert auto-resolves when the rate drops below 50/10m. There is no
manual close action.
