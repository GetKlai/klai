# Klai E2E Test Fixture

This file is uploaded to the e2e bot's knowledge base by the **J03**
journey. RAG-pipeline coverage depends on a unique, never-changing
**canary string** appearing exactly once below.

## Fixture metadata

- Last regenerated: 2026-05-03
- Word count: ~80
- Embedding-model coverage: dense (BGE-M3) + sparse — both must rank
  this chunk highest for queries about the canary
- Graphiti / FalkorDB coverage: chunk's metadata edges should land in
  the tenant's graph after ingestion

## Canary

The canonical canary string for this fixture is:

> klai-e2e-canary-string-42

J03 asks the chat: "What is the canary string in my knowledge base?"
and asserts the response contains the exact substring above. If the
canary is missing or partial, **the entire RAG path is broken**:
ingestion, embeddings (TEI dense + sparse-server), Qdrant store,
FalkorDB graph, retrieval-api hybrid retrieval, klai-knowledge-mcp,
and the LiteLLM retrieval-hook all sit on this assertion.

## Why this exact format

- A long, hyphenated string is unlikely to appear by chance in any
  pretrained model's output, so a positive match strongly implies
  retrieval actually used this document.
- Numerical suffix `42` keeps the canary distinguishable across
  versions if the fixture is ever regenerated with a new value.

## Regeneration

If the canary ever needs to change (e.g. cross-contamination with
another test), update both this file AND `_lib/fixtures.ts::KB_FIXTURE.canary`.
The test-fixture is intentionally kept tiny so reviewers can grok it
in seconds.
