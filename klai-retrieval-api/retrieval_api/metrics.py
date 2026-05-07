"""Prometheus metrics for the Klai retrieval pipeline.

Exposes histograms for per-step latency so Grafana dashboards can show
p50/p95/p99 per pipeline stage. Mounted at /metrics in main.py.

Steps tracked:
  coref    — coreference resolution (LiteLLM rewrite)
  embed    — dense + sparse embedding (TEI)
  qdrant   — Qdrant hybrid vector search
  graph    — Graphiti / FalkorDB graph search
  rerank   — cross-encoder reranking (TEI reranker)
  total    — full pipeline end-to-end

SPEC-SEC-010 REQ-7.2 adds security counters:
  auth_rejected{reason} — auth middleware rejections
  rate_limited{method}  — rate-limit rejections
  cross_user_rejected   — body user_id != JWT sub
  cross_org_rejected    — body org_id != JWT resourceowner
"""

from prometheus_client import Counter, Histogram

# Latency buckets covering expected range: 50ms → 60s
# Coarse at the top end because graph/rerank can be slow on CPU.
_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, float("inf"))

step_latency_seconds = Histogram(
    "klai_retrieval_step_seconds",
    "Retrieval pipeline per-step latency in seconds",
    ["step"],
    buckets=_BUCKETS,
)

retrieval_requests_total = Counter(
    "klai_retrieval_requests_total",
    "Total retrieval requests",
    ["scope", "bypassed"],
)

retrieval_chunks_total = Histogram(
    "klai_retrieval_chunks_returned",
    "Chunks returned per retrieval request",
    ["scope"],
    buckets=(0, 1, 2, 3, 5, 10, 20, float("inf")),
)

# SPEC-SEC-010 REQ-7.2 — security counters
auth_rejected_total = Counter(
    "retrieval_api_auth_rejected_total",
    "Total auth rejections by reason",
    ["reason"],
)

rate_limited_total = Counter(
    "retrieval_api_rate_limited_total",
    "Total rate-limit rejections by auth method",
    ["method"],
)

cross_user_rejected_total = Counter(
    "retrieval_api_cross_user_rejected_total",
    "Requests rejected because body user_id != JWT sub",
)

cross_org_rejected_total = Counter(
    "retrieval_api_cross_org_rejected_total",
    "Requests rejected because body org_id != JWT resourceowner",
)

# SPEC-SEC-HYGIENE-001 REQ-40 — events that overflow the bounded
# ``services.events._pending`` set are dropped to prevent OOM during
# flood conditions; this counter makes drops observable in Prometheus.
retrieval_events_dropped_total = Counter(
    "retrieval_events_dropped_total",
    "Product events dropped because the pending-tasks cap was reached",
)

# SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-08 — chunks removed by the hard
# quality-score floor (Phase E). Most increments are zero in steady state;
# spikes signal either:
#   - degrade-mode tenants whose backfill is still running (expected),
#   - or an ingest-time detector miss (regression alert).
# Labelled by org_id so per-tenant pollution is distinguishable from a
# system-wide regression. We do NOT label by chunk_id (high-cardinality);
# the chunk IDs are still emitted at DEBUG level in the per-request logs
# for forensic lookup.
quality_floor_filtered_total = Counter(
    "klai_retrieval_quality_floor_filtered_total",
    "Chunks removed by the quality-floor filter (SPEC-INGEST-LOGIN-WALL-DETECT-001)",
    ["org_id"],
)

# SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1 / REQ-8 — confidence band
# distribution per tenant. Bands: high (max rerank ≥ high_threshold),
# medium (between thresholds), low (< low_threshold), unknown (reranker
# disabled, fallback, or empty result). Unknown should be a small fraction
# of total in healthy operation; spikes indicate reranker instability.
retrieval_confidence_band_total = Counter(
    "retrieval_confidence_band_total",
    "Retrieval responses bucketed by reranker-score confidence band",
    ["band", "org_id"],
)

# SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-3 / REQ-8 — link-expansion
# survival rate. ``hit`` = at least one expanded chunk made the served
# top-K. ``miss`` = link-expand ran (added ≥ 1 chunk to candidates) but
# none survived rerank + source-aware-select + quality-boost. Only
# incremented when link-expand actually contributed candidates; requests
# with link_expand_count == 0 are not counted.
retrieval_link_expand_top_k_total = Counter(
    "retrieval_link_expand_top_k_total",
    "Link-expand contribution to served top-K (hit/miss per tenant)",
    ["outcome", "org_id"],
)
