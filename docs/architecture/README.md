# Architecture

Platform-brede architectuurbeslissingen die relevant zijn voor alle Klai repos.

## Bestanden

| Bestand | Inhoud |
|---------|--------|
| [platform.md](platform.md) | Stack-keuzes, server layout, fasen, modellen, knowledge system design principles, RAG stack, Compatibility Review |
| [klai-knowledge-architecture.md](klai-knowledge-architecture.md) | Klai Knowledge platform architectuur — §§0-14 incl. knowledge model, ingestion, retrieval (+ evidence-weighted scoring §7.4), gap detection, AI interface, multi-tenancy |
| [knowledge-ingest-flow.md](knowledge-ingest-flow.md) | **Engineering reference** — het draaiende ingest-systeem op core-01: content sources, pipeline phases, Qdrant scope, Procrastinate scheduling, HyPE, docling |
| [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md) | **Engineering reference** — het draaiende retrieval-systeem: LiteLLM hook, 6-staps pipeline, context injection, UI toggles, config values |

> **Engineering references zijn leidend.** Bij verschil tussen een architecture-doc en een engineering reference geldt de engineering reference — die is geverifieerd tegen de live code.

## Research

Achterliggende onderzoeksdocumenten die de architecture-beslissingen onderbouwen:

| Bestand | Inhoud |
|---------|--------|
| [../research/knowledge-system-fundamentals.md](../research/knowledge-system-fundamentals.md) | 16 empirisch onderbouwde bevindingen over kennissystemen — entity types, retrieval, graph layer, taxonomie, monitoring (NL) |
| [../research/knowledge-pipeline-architecture.md](../research/knowledge-pipeline-architecture.md) | Uitgebreid design-document: extractieschema, prompt-strategie, GDPR, gap detection, widget SDK evaluatie, publication layer (NL) |

De conclusies uit deze documenten zijn gedestilleerd in `platform.md §Knowledge System Design Principles`. De research-docs zijn het archief voor wie de redenering wil traceren.

Het research programme dat de evidence-weighted scoring onderbouwt leeft in [`docs/research/`](../research/README.md). Vier dimensies onderzocht: content type, assertion mode, temporal decay, cross-source corroboration.

## Scope

Deze directory bevat beslissingen over het **gehele Klai-platform** — niet website-specifiek, niet repo-specifiek.
Project-specifieke architectuurdocumentatie leeft in het eigen `docs/` van dat project.
