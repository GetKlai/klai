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
