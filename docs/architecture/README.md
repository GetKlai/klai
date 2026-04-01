# Architecture

Platform-wide architectural decisions relevant to all Klai repositories.

## Files

| File | Contents |
|------|----------|
| [platform.md](platform.md) | Stack choices, server layout, phases, models, knowledge system design principles, RAG stack, Compatibility Review |
| [klai-knowledge-architecture.md](klai-knowledge-architecture.md) | Klai Knowledge platform architecture — §§0-14 incl. knowledge model, ingestion, retrieval (+ evidence-weighted scoring §7.4), gap detection, AI interface, multi-tenancy |
| [knowledge-ingest-flow.md](knowledge-ingest-flow.md) | **Engineering reference** — the running ingest system on core-01: content sources, pipeline phases, Qdrant scope, Procrastinate scheduling, HyPE, docling |
| [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md) | **Engineering reference** — the running retrieval system: LiteLLM hook, 6-step pipeline, context injection, UI toggles, config values |

> **Engineering references are authoritative.** When there is a conflict between an architecture doc and an engineering reference, the engineering reference wins — it is verified against live code.

## Research

Background research documents that underpin the architecture decisions:

| File | Contents |
|------|----------|
| [../research/knowledge-system-fundamentals.md](../research/knowledge-system-fundamentals.md) | 16 empirically grounded findings on knowledge system design — entity types, retrieval, graph layer, taxonomy, monitoring |
| [../research/knowledge-pipeline-architecture.md](../research/knowledge-pipeline-architecture.md) | Detailed design document: extraction schema, prompt strategy, GDPR, gap detection, widget SDK evaluation, publication layer |

The conclusions from these documents are distilled in `platform.md § Knowledge System Design Principles`. The research docs are the archive for anyone who wants to trace the reasoning.

The research programme underpinning evidence-weighted scoring lives in [`docs/research/`](../research/README.md). Four dimensions studied: content type, assertion mode, temporal decay, cross-source corroboration.

## Scope

This directory contains decisions about the **entire Klai platform** — not website-specific, not repo-specific.
Project-specific architecture documentation lives in that project's own `docs/` directory.
