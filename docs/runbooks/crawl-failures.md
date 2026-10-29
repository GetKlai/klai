# Crawl failures

> Procedure for the alerts in `deploy/grafana/provisioning/alerting/crawl-rules.yaml`:
> `crawl_crawler_errors`, `crawl_fleet_signature`, `crawl_success_below_baseline`
> and `crawl_jobs_silent`.

All four read `knowledge.crawl_job_health`, a superuser-owned view over `knowledge.crawl_jobs` (the table is row-level-secured and holds crawl config and cookies), except that `crawl_jobs_silent` also reads `portal_scheduled_crawlers`. Both views are defined in `deploy/grafana/sql/grafana-reader-setup.sql`.

Per job the view carries `fetch_total`, `crawler_side_failures`, `top_signature` (the most frequent `error_type:status_code`) and `top_signature_share`. **Crawler-side** means raised by our own request to crawl4ai: a 4xx or 5xx `httpx.HTTPStatusError`, or an `httpx.ConnectError`, `ReadTimeout` or `ConnectTimeout`. A page the target site answered with an error is recorded with `error_type = 'crawl4ai:<reason>'` and is not crawler-side; neither is a 429, which is a site asking for a slower pace.

## What each alert means

| Alert | Fires when | Severity |
|---|---|---|
| `crawl_crawler_errors` | The latest job of a source finished in the last 26 h with at least 20 fetch outcomes, of which 20% or more were crawler-side. | critical, pages (`page="true"`) |
| `crawl_fleet_signature` | In the last 24 h, jobs of at least 3 different sources have the same `error_type:status_code` as half or more of their outcomes. Unrelated sites do not fail identically, so the cause is on our side. | critical |
| `crawl_success_below_baseline` | The latest job ingested under 70% of the median `pages_done` of the source's last 5 completed jobs before it (needs 3 or more of those, and a median of at least 1). Catches partial breakage that has no error signature. | warning, email only |
| `crawl_jobs_silent` | A web connector that is enabled, active and scheduled every day has no crawl job updated in the last 30 h. | critical |

## Query 1: what happened to one knowledge base

```bash
ssh core-01
docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  SELECT status, reason, fetch_total, crawler_side_failures, top_signature,
         top_signature_share, pages_total, pages_done, to_timestamp(updated_at) AS finished
    FROM knowledge.crawl_job_health
   WHERE kb_slug = '<kb_slug>'
   ORDER BY updated_at DESC LIMIT 10;"
```

`reason` is `error_summary->>'reason'`:

| reason | Meaning |
|---|---|
| `crawler_config_rejected` | crawl4ai refused our own request (a 4xx raised by the crawl4ai API); the job ends `failed`. A deployment bug, every knowledge base fails at once. See below. |
| `fetch_failure_dominant` | The crawler ran and most pages failed to fetch: the site, DNS, a timeout. Per knowledge base. |
| `boilerplate_or_authwall_dominant` | Pages were fetched but are login walls or boilerplate. Cookies expired or missing. |

## Query 2: is it every knowledge base

```bash
docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  SELECT top_signature, count(DISTINCT org_id || '/' || kb_slug) AS sources,
         round(avg(top_signature_share), 2) AS avg_share
    FROM knowledge.crawl_job_health
   WHERE updated_at >= extract(epoch FROM now() - interval '24 hours')
     AND top_signature IS NOT NULL
   GROUP BY 1 ORDER BY 2 DESC;"
```

The same signature on several unrelated sources is the crawler or our request to it, not the sites.

## The 2026-10-23 cause, and what would have caught it

From 2026-10-23 02:01 every nightly crawl of every knowledge base fetched 0 pages, six nights in a row. crawl4ai 0.9.5 answered HTTP 400 `Rejected config: a JavaScript wait_for is not permitted from an untrusted request` to the `wait_for` built in `klai-knowledge-ingest/knowledge_ingest/crawl4ai_config.py`. The jobs were recorded as `failed_partial` with `fetch_failure_dominant` ("the site may be blocking automated access"), and no rule read crawl jobs. Since then a refusal by the crawler's own config check ends the job as `failed` with reason `crawler_config_rejected`, and `deploy/tests/crawl4ai-config-contract.sh` posts the real config to the pinned image in CI.

Against that night:

- `crawl_crawler_errors` and `crawl_fleet_signature` on the first night, 2026-10-23, as soon as the first evaluation after the jobs finished ran. The failures were raised 400s from crawl4ai (`httpx.HTTPStatusError:400`) on every knowledge base. Condition for the first: the jobs recorded 20 or more outcomes. The second has no such floor but needs 3 sources.
- `crawl_success_below_baseline` on the first night too: every source had completed jobs before 2026-10-23, so a baseline existed and the 0 pages fell under 70% of it. It would have stayed firing on every following night, because only completed jobs form the baseline.
- `crawl_jobs_silent` would NOT have fired: the scheduler kept creating jobs, they just failed. It exists for the opposite failure, a scheduler that no longer starts anything; it fires 30 h after the last job of a daily-scheduled source.

For `crawler_config_rejected`: check the crawl4ai image tag in `deploy/docker-compose.yml`, run `sh deploy/tests/crawl4ai-config-contract.sh` and read the body it prints, and compare against the crawl4ai release notes for the version the image runs.

## Politeness towards crawled hosts

Each crawl4ai request carries one URL and a host receives at most one request per second, halved (down to 0.2 per second) after a 429 or anti-bot response and raised again after clean crawls. Do not batch URLs per request to speed a crawl up: crawl4ai fetches a multi-URL request with 20 parallel sessions, which caused 71 429s in 244 fetches on 2026-10-28.

## Stored session cookies about to expire

klai-connector calls the keep-alive (`POST /ingest/v1/crawl/keep-alive` on knowledge-ingest) every 30 minutes. It fetches the probe page with the site's own cookies and writes any cookie the site renewed back to the connector's credentials once the page proves the session matters. A pasted cookie the site never renews still lapses on its own date, so the alert `crawl_session_cookie_expiring` (`crawl-session-rules.yaml`, component `crawl`) fires when `crawl_keepalive_cookie_expiring` (logged on every tick, never with the cookie value) names a cookie of the site that lapses within 7 days; paste fresh cookies before the `expires` date and the alert clears.

## crawl_jobs_silent: dead scheduler

The scheduler is klai-connector's APScheduler; see `docs/runbooks/connector-schedules.md`. Check its log for `Scheduler refreshed with N scheduled connectors` and for errors on that connector. Sources scheduled weekly, or not at all, are not covered by this alert.

## Run one probe crawl

Trigger a sync of the web connector of one knowledge base (see `docs/runbooks/connector-schedules.md` for how syncs start), then follow the job:

```bash
docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  SELECT status, reason, fetch_total, crawler_side_failures, pages_total, pages_done,
         to_timestamp(updated_at)
    FROM knowledge.crawl_job_health
   WHERE kb_slug = '<kb_slug>' ORDER BY updated_at DESC LIMIT 1;"
```

The crawl is healthy when `pages_done` rises above 0, `crawler_side_failures` stays at 0 and the job ends `completed`. Logs: VictoriaLogs, `service:knowledge-ingest AND message:crawl4ai_request_failed`; the `response_body` field carries the crawler's own answer.

Do not start crawls of every knowledge base to test a fix. One probe is enough; the nightly schedule does the rest.
