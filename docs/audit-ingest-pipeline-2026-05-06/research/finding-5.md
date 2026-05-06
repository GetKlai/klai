# Finding 5 research: silent debug logging on centroid fallback

## Code verification

**Exception scope — verified BROAD.**

`klai-knowledge-ingest/knowledge_ingest/routes/ingest.py` lines 356-374:

```python
try:
    centroids = load_centroids(req.org_id, req.kb_slug)
    if centroids:
        from knowledge_ingest import embedder as _embedder
        doc_vectors = await _embedder.embed([req.content[:512]])
        doc_vec = doc_vectors[0] if doc_vectors else None
        if doc_vec is not None:
            centroid_result = classify_by_centroid(
                embedding=doc_vec,
                centroids=centroids,
                threshold=settings.taxonomy_centroid_match_threshold,
                taxonomy_node_ids={n.id for n in taxonomy_nodes},
            )
            if centroid_result is not None:
                taxonomy_node_ids = centroid_result
                centroid_matched = True
except Exception:
    logger.debug("centroid_lookup_failed", exc_info=True)
```

The `except Exception:` block catches every possible failure in the entire centroid fast-path:
- `load_centroids` I/O errors (corrupt file, disk full, permission denied)
- `_embedder.embed` network errors (TEI GPU service at `gpu-01:7997` down or timing out)
- `classify_by_centroid` computation errors (numpy version mismatch, malformed centroid blob, dimension mismatch)
- `asyncio.CancelledError` is NOT caught (it inherits from `BaseException`, not `Exception`), which is the only correct exclusion here

**Production log level — confirmed INFO floor.**

`knowledge_ingest/logging_setup.py` line 62:
```python
root_logger.setLevel(logging.INFO)
```

The root logger is set to `INFO`. structlog is wired through this same root logger via `ProcessorFormatter`. Debug events are filtered at stdlib level before they reach any transport. VictoriaLogs never receives them.

**Alert coverage — none for this event.**

`deploy/grafana/provisioning/alerting/ingest-rules.yaml` contains one rule for knowledge-ingest: `obs-001-ingest-error-rate-elevated` (SPEC-OBS-001 R16). This alert triggers on `level:error` events exceeding 10 in 10 minutes. Since `centroid_lookup_failed` logs at `debug`, it is invisible to both:
- VictoriaLogs (filtered before emit)
- The Grafana alert (which queries `level:error`)

**Other similar patterns found in production code (not tests):**

| File | Event | Verdict |
|---|---|---|
| `routes/ingest.py:76` | `frontmatter_yaml_parse_error` | Acceptable at debug — corrupt YAML frontmatter is a user content problem, not a system failure |
| `routes/ingest.py:177` | `belief_time_parse_error` | Acceptable at debug — user-supplied field, non-critical |
| `routes/ingest.py:374` | `centroid_lookup_failed` | **WRONG LEVEL** — system component failure with cost impact |
| `routes/crawl.py:217` | `auth_guard_login_indicator_detection_skipped` | Borderline — LLM detection on crawl preview is optional; acceptable as debug |

The centroid case is the only one in production code that swallows an exception caused by an infrastructure or algorithm failure (not by user-supplied input being invalid) at debug level.

---

## Current behavior

When the centroid fast-path fails for any reason:

1. The exception is caught and suppressed at `debug` level.
2. The log event never reaches VictoriaLogs (filtered at `root_logger.setLevel(logging.INFO)`).
3. `centroid_matched` remains `False`.
4. Execution falls through to `classify_document()` — an LLM call (likely `klai-fast`) that costs tokens, adds latency, and consumes the 1 req/s upstream rate limit shared with Graphiti enrichment.
5. No counter, no metric, no alert. The operator has no signal that the centroid subsystem has failed.

The fallback is not zero-cost: `classify_document` is the expensive path the centroid system was introduced (SPEC-KB-024) specifically to avoid. Every document that should use the centroid fast-path but cannot — because of a broken blob, a TEI outage, or a numpy incompatibility — silently pays the full LLM price.

---

## Industry standard (2026)

### Log-level conventions: designed fallback vs unexpected degradation

The central distinction in the SRE and logging literature is between **expected** fallback and **unexpected** degradation:

- **DEBUG**: Detailed operational information useful only during active development or debugging. An exception caught at `debug` communicates "this is a routine, anticipated event with no operational consequence." That framing is false for `centroid_lookup_failed`.
- **WARNING**: Something unexpected occurred but the system self-recovered. The system can continue, but a human should eventually look. This is the canonical level for "tried the fast path, something broke, fell back to the slow path."
- **ERROR**: A failure that affected functionality and may need immediate attention.

The [Better Stack log-levels guide](https://betterstack.com/community/guides/logging/log-levels-explained/) states explicitly: "Errors with a potential for recovery, such as network connectivity issues with automated retry mechanisms, can be logged at the WARN level." It further notes that WARN "may be elevated to ERROR if recovery is unsuccessful after several attempts."

The [Edge Delta log levels guide](https://edgedelta.com/company/blog/log-debug-vs-info-vs-warn-vs-error-and-fatal) separates the two clearly: "If the failure is handled gracefully and the user experience is unaffected, log it at WARN or lower" — but "lower" means INFO for user-visible events, not DEBUG. DEBUG is reserved for implementation details, not for failure events.

The [Google SRE Monitoring chapter](https://sre.google/sre-book/monitoring-distributed-systems/) distinguishes between symptoms (user-visible) and causes (infrastructure). The centroid failure is a cause-level event: it is invisible to the user (they still get a classification) but it reveals an infrastructure-level problem. SRE practice is to instrument cause-level events at a level that surfaces them in dashboards, not to suppress them below the production log floor.

### Structlog conventions

structlog's own [exception documentation](https://www.structlog.org/en/stable/exceptions.html) and [logging best practices page](https://www.structlog.org/en/stable/logging-best-practices.html) recommend:
- Use `logger.warning("event_name", exc_info=True)` for caught exceptions that represent unexpected conditions.
- Use `logger.exception("event_name")` (which is `error` + `exc_info=True`) when the exception should trigger alerting.
- `logger.debug("event_name", exc_info=True)` is appropriate only for development-time instrumentation that is explicitly not expected in production log streams.

The structlog pattern `logger.warning("centroid_lookup_failed", exc_info=True)` is standard for "caught exception, recovered, but you should know." It includes the traceback (for diagnosis) while not triggering `level:error` alerts.

### Tiered classification observability

The standard observability pattern for a tiered classifier (fast path → slow path) tracks:

1. **A counter per tier**: `centroid_hit_total`, `centroid_miss_total`, `centroid_error_total` (distinct from miss — miss means below threshold, error means the fast path failed entirely).
2. **A ratio metric**: `centroid_error_rate = centroid_error_total / (centroid_hit_total + centroid_miss_total + centroid_error_total)`. A persistent non-zero error rate indicates a broken fast-path subsystem.
3. **An anomaly threshold**: If the error rate crosses a threshold (e.g. >5% over 10 minutes), alert. This is preferable to alerting on every individual error because occasional embedding timeouts are expected; sustained failures are not.

RAG pipeline observability platforms (Langfuse, Arize Phoenix) all track retrieval-path fallback rates as first-class metrics. The Klai knowledge-ingest pipeline currently has no equivalent for the centroid classifier.

---

## Fix recommendations

**Minimum viable fix (log level change only):**

Change line 374 of `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`:

```python
# Before
logger.debug("centroid_lookup_failed", exc_info=True)

# After
logger.warning(
    "centroid_lookup_failed",
    exc_info=True,
    org_id=req.org_id,
    kb_slug=req.kb_slug,
)
```

This single change makes the failure visible in VictoriaLogs (which already collects `level:warning` events) and in any future `level:warning`-based alerts without requiring any infrastructure changes.

**Structured fields to add:**

At minimum: `org_id` and `kb_slug`. These fields are already bound on the request context via `RequestContextMiddleware` (via `X-Org-ID`), but binding them explicitly in the log call is defensive and makes the VictoriaLogs query `org_id:<slug> AND message:centroid_lookup_failed` work even if the context was cleared.

**Counter metric (recommended follow-up):**

Add a lightweight in-memory or Prometheus counter:
```python
# At module level or via a simple structlog bound_logger approach
logger.warning("centroid_lookup_failed", exc_info=True, org_id=req.org_id, kb_slug=req.kb_slug)
# Optionally: increment a Prometheus counter
# centroid_errors_total.labels(org_id=req.org_id).inc()
```

A LogsQL-based Grafana alert on `service:knowledge-ingest AND level:warning AND message:centroid_lookup_failed` with a count >5 in 10 minutes would catch persistent failures without noisy one-off alerts.

**Alert rule (recommended follow-up):**

Add to `deploy/grafana/provisioning/alerting/ingest-rules.yaml`:
```yaml
- uid: obs-001-centroid-error-rate
  title: ingest_centroid_error_rate_elevated
  # expr: service:knowledge-ingest AND level:warning AND message:centroid_lookup_failed
  # fire if count > 5 in 10m
```

This follows the existing `obs-001-ingest-error-rate-elevated` pattern already established in that file.

---

## Risk assessment

**How would we currently know this is happening?**

We would not, unless:
- An operator happens to restart the container with `LOG_FORMAT=console` and watches stdout directly.
- A user notices that taxonomy classification is slower than expected and reports it.
- An operator correlates a spike in `classify_document` LLM call volume with an outage of the TEI embedding service on `gpu-01`.

None of these is a reliable signal. The failure is structurally invisible in production.

**How long could it stay broken?**

Indefinitely, or until the centroid subsystem is exercised in a context where someone notices the LLM fallback cost. The centroid system was introduced for cost efficiency (SPEC-KB-024). If it breaks silently, all documents with a matching taxonomy fall back to LLM classification. On a high-ingest org, this could mean hundreds of extra LLM calls per hour — which would show up as elevated token cost but would not be correlated to the centroid failure without log visibility.

The VictoriaLogs 30-day retention means that even a retrospective diagnosis would be impossible if the failure persisted for more than 30 days: the logs that would have shown the failure are gone.

This is a confirmed instance of the `data-before-code` and `silent-degrade` anti-patterns documented in `.claude/rules/klai/pitfalls/process-rules.md`. The `fail-open-auth` pitfall section explicitly names silent degradation as "worse than a loud error" for features the user believes are active.

---

## References

- [Better Stack: Log Levels Explained](https://betterstack.com/community/guides/logging/log-levels-explained/)
- [Edge Delta: Log Debug vs Info vs Warn vs Error and Fatal](https://edgedelta.com/company/blog/log-debug-vs-info-vs-warn-vs-error-and-fatal)
- [structlog: Logging Best Practices](https://www.structlog.org/en/stable/logging-best-practices.html)
- [structlog: Exception Handling](https://www.structlog.org/en/stable/exceptions.html)
- [Google SRE Book: Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/)
- [SRE School: Graceful Degradation](https://sreschool.com/blog/graceful-degradation/)
- [middleware.io: Understanding Log Levels for Better Observability](https://middleware.io/blog/log-levels-guide/)
- [Langfuse: RAG Observability and Evals](https://langfuse.com/blog/2025-10-28-rag-observability-and-evals)
