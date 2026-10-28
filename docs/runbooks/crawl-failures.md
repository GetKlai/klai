# Crawl failures

> Procedure for the alerts `crawl_jobs_failing` and `crawl_jobs_silent`
> (`deploy/grafana/provisioning/alerting/crawl-rules.yaml`).

## What the alerts mean

- **crawl_jobs_failing**: for this knowledge base the latest finished crawl job in the last 26 hours ended `failed` or `failed_partial` with `pages_done = 0`. Nothing was fetched, so the knowledge base is not being refreshed.
- **crawl_jobs_silent**: this knowledge base had crawl jobs in the 7 days before, and none in the last 30 hours. The scheduler did not start it. A knowledge base crawled less often than daily, or one whose connector was removed, also fires until it leaves the 7-day window.

Both read `knowledge.crawl_job_health`, a superuser-owned view over `knowledge.crawl_jobs` (the table itself is row-level-secured and holds crawl config).

## Query 1: what happened to one knowledge base

```bash
ssh core-01
docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  SELECT status, reason, pages_total, pages_done, to_timestamp(updated_at) AS finished
    FROM knowledge.crawl_job_health
   WHERE kb_slug = '<kb_slug>'
   ORDER BY updated_at DESC LIMIT 10;"
```

`reason` is `error_summary->>'reason'`:

| reason | Meaning |
|---|---|
| `crawler_config_rejected` | crawl4ai refused our own request (a 4xx raised by the crawl4ai API). A deployment bug, every knowledge base fails at once. See below. |
| `fetch_failure_dominant` | The crawler ran and most pages failed to fetch: the site, DNS, a timeout. Per knowledge base. |
| `boilerplate_or_authwall_dominant` | Pages were fetched but are login walls or boilerplate. Cookies expired or missing. |

## Query 2: is it every knowledge base

```bash
docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  SELECT date_trunc('day', to_timestamp(updated_at)) AS day, status, reason,
         count(*) AS jobs, sum(pages_done) AS pages
    FROM knowledge.crawl_job_health
   WHERE updated_at >= extract(epoch FROM now() - interval '7 days')
   GROUP BY 1, 2, 3 ORDER BY 1 DESC, 4 DESC;"
```

If every knowledge base flipped to 0 pages on the same night, it is the crawler or our request to it, not the sites.

## The 2026-10-23 cause

From 2026-10-23 02:01 every nightly crawl of every knowledge base fetched 0 pages, six nights in a row. crawl4ai 0.9.5 answered HTTP 400 `Rejected config: a JavaScript wait_for is not permitted from an untrusted request` to the `wait_for` built in `klai-knowledge-ingest/knowledge_ingest/crawl4ai_config.py`. The jobs were recorded as `failed_partial` with `fetch_failure_dominant` ("the site may be blocking automated access"), and no rule read crawl jobs. Since then a refusal by the crawler's own config check ends the job as `failed` with reason `crawler_config_rejected`, and `deploy/tests/crawl4ai-config-contract.sh` posts the real config to the pinned image in CI.

For `crawler_config_rejected`: check the crawl4ai image tag in `deploy/docker-compose.yml`, run `sh deploy/tests/crawl4ai-config-contract.sh` and read the body it prints, and compare against the crawl4ai release notes for the version the image runs.

## Run one probe crawl

Trigger a sync of the web connector of one knowledge base (see `docs/runbooks/connector-schedules.md` for how syncs start), then follow the job:

```bash
docker exec klai-core-postgres-1 psql -U klai -d klai -c "
  SELECT status, reason, pages_total, pages_done, to_timestamp(updated_at)
    FROM knowledge.crawl_job_health
   WHERE kb_slug = '<kb_slug>' ORDER BY updated_at DESC LIMIT 1;"
```

The crawl is healthy when `pages_done` rises above 0 and the job ends `completed`. Logs: VictoriaLogs, `service:knowledge-ingest AND message:crawl4ai_request_failed`; the `response_body` field carries the crawler's own answer.

Do not start crawls of every knowledge base to test a fix. One probe is enough; the nightly schedule does the rest.
