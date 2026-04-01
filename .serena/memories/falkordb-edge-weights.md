# FalkorDB Edge Weight Capabilities

Research completed 2026-03-31.

## What FalkorDB CAN do with edge weights

1. **ORDER BY weight** — Standard Cypher: `MATCH ()-[r:RELATES_TO]->() RETURN r ORDER BY r.weight DESC`
2. **Weighted shortest path** — `algo.SPpaths` and `algo.SSpaths` support `weightProp` parameter:
   ```cypher
   MATCH (a {name:'X'}), (b {name:'Y'})
   CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: ['RELATES_TO'], weightProp: 'weight'})
   YIELD path, pathWeight RETURN path, pathWeight
   ```
3. **Minimum spanning forest** — `algo.MSF` with weights
4. **Custom Cypher queries** — Context expansion (neighbors by weight), filtering, score-boosting

## What FalkorDB CANNOT do with weights

1. **Weighted PageRank** — `algo.pagerank` has no `weightProp` parameter
2. **Weighted community detection** — `algo.CDLP` is unweighted
3. **Weighted centrality** — `algo.BC` (betweenness centrality) is unweighted
4. **No GDS-like plugin system** — Unlike Neo4j's Graph Data Science library (65+ algorithms)

## Available algorithms (total: 8)

| Algorithm | Weighted? | Function |
|---|---|---|
| Shortest path (single-pair) | YES | `algo.SPpaths` |
| Shortest path (single-source) | YES | `algo.SSpaths` |
| Minimum spanning forest | YES | `algo.MSF` |
| BFS | NO | `algo.BFS` |
| PageRank | NO | `algo.pagerank` |
| Betweenness centrality | NO | `algo.BC` |
| Weakly connected components | NO | `algo.WCC` |
| Community detection (label propagation) | NO | `algo.CDLP` |

## Practical use in Klai

For the Klai knowledge graph (Hebbian edge weights from co-mentions):
- **Context expansion**: Traverse neighbors sorted by weight → most-related entities first
- **Score boosting**: In RAG retrieval, boost chunks containing entities connected by high-weight edges
- **Weighted path finding**: Find strongest connection paths between concepts
- **Filtering**: Ignore low-weight edges (noise) in graph traversals

Custom Cypher is the primary tool — FalkorDB's built-in algorithms are limited but the weighted shortest path and ORDER BY cover the most important use cases.
