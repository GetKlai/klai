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
