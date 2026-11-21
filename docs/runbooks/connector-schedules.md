# Connector schedules (nightly sync)

A connector syncs automatically when `portal_connectors.schedule` holds a
5-field crontab expression (e.g. `0 2 * * *`). The portal is the only source
of truth; klai-connector's APScheduler reads
`GET /internal/scheduled-connectors` at startup and every 5 minutes, so a
schedule added, changed, disabled or removed in the portal takes effect
within 5 minutes without a restart. Only connectors of org-scoped KBs are
scheduled; personal KBs carry an item quota that a scheduled sync cannot
check, so they stay manual. The scheduler is created without a timezone
(`klai-connector/app/services/scheduler.py:42`) and the compose service sets
no `TZ`, so cron times follow the local zone of the running container; check
that zone before converting a time. For weekly schedules use named
weekdays (`0 2 * * sun`): APScheduler 3.x counts numeric `0` as Monday, not
Sunday as in POSIX cron.

## Enable for a tenant

Via the portal API (as KB owner):

```
PATCH /api/app/knowledge-bases/{kb_slug}/connectors/{connector_id}
{"schedule": "0 2 * * *"}
```

Or directly in the portal DB (operator):

```sql
UPDATE portal_connectors SET schedule = '0 2 * * *'
 WHERE id IN ('<connector-uuid>', ...);
```

A malformed schedule (not exactly 5 fields) is rejected with 422
`invalid_schedule`. Send an empty string `""` to disable the schedule (a JSON
`null` is treated as "field not provided" and leaves it unchanged). A
5-field string with out-of-range values (e.g. `99 99 * * *`) is stored but
rejected by APScheduler at refresh time and logged as an error in
klai-connector; check the log after setting a schedule.
Stagger web crawlers of one tenant by a few minutes (`0 2`, `10 2`, …) so
they do not all hit crawl4ai at the same second.

## Verify

- klai-connector log within 5 minutes:
  `Scheduler refreshed with N scheduled connectors`.
- After the first firing: `Scheduled sync triggered for connector <id>`,
  then `portal_connectors.last_sync_at` / `last_sync_status` update and a
  new row in `connector.sync_runs` with `org_id` set.
- A firing is skipped with a warning when a run for that connector is
  still `RUNNING`.

## Behaviour and limits

- Web crawls skip pages whose raw HTML and extracted text are unchanged
  (dual hash), so a nightly run costs only new or changed pages.
- Pages that disappeared from the site are removed after a crawl that
  completed and fetched everything it should. A crawl that saw 404 or 410
  pages and would retire more than 20% of the stored pages (at least 5) skips
  the cleanup and logs it; the next clean crawl retires what is really gone
  (`klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py:126-139`,
  [knowledge-ingest-flow.md](../architecture/knowledge-ingest-flow.md), 4.2).
- A failing connector (e.g. expired cookies) fails every night; the status
  is visible in the portal connector list.
