# FalkorDB edge weights in Klai (verified 2026-08-18)

This note records the behavior implemented in Klai. It does not claim support for FalkorDB algorithms that Klai does not call.

## Write path

`klai-knowledge-ingest/knowledge_ingest/graph.py::_update_edge_weights` reinforces `RELATES_TO` edges after a successful Graphiti episode. For each pair of entities mentioned in that episode, its Cypher query sets:

```cypher
SET r.weight = COALESCE(r.weight, 0) + 1
```

The operation is best-effort: a weight-update failure is logged but does not fail ingestion.

## Read path

`klai-retrieval-api/retrieval_api/services/graph_search.py::_convert_results` reads the `weight` carried by a Graphiti edge result and boosts the semantic score when the value is positive:

```text
boosted score = semantic score * (1 + 0.1 * log1p(weight))
```

The logarithm keeps repeated co-mentions from dominating semantic relevance. A missing, zero, or negative weight leaves the semantic score unchanged.

## Regression coverage

`klai-retrieval-api/tests/test_graph_search.py` covers the positive-weight boost and the unweighted fallback. When changing the write or read path, keep the edge property name and scoring contract aligned and run that test file.

## Historical note

The removed Serena memory dated 2026-03-31 also catalogued built-in FalkorDB graph algorithms. That catalogue was not tied to Klai code or a pinned server version, so it was deliberately not retained as operational guidance.
