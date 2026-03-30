# Architecture

Platform-brede architectuurbeslissingen die relevant zijn voor alle Klai repos.

## Bestanden

| Bestand | Inhoud |
|---------|--------|
| [platform.md](platform.md) | Stack-keuzes, server layout, fasen, modellen, RAG, Compatibility Review |
| [klai-knowledge-architecture.md](klai-knowledge-architecture.md) | Klai Knowledge platform architectuur — §§0-14 incl. knowledge model, ingestion, retrieval (+ evidence-weighted scoring §7.4), gap detection, AI interface, multi-tenancy |
| [knowledge-ingest-flow.md](knowledge-ingest-flow.md) | Engineering reference voor het draaiende ingest/retrieval systeem op core-01 |
| [knowledge-system-fundamentals.md](knowledge-system-fundamentals.md) | Fundamentals van een goed kennissysteem — 16 bevindingen onderbouwd door onderzoek |
| [knowledge-pipeline-architecture.md](knowledge-pipeline-architecture.md) | Pipeline architectuur: extractie, ontologie, hybride retrieval |

## Research

Het research programme dat de evidence-weighted scoring onderbouwt leeft in [`docs/research/`](../research/README.md). Vier dimensies onderzocht: content type, assertion mode, temporal decay, cross-source corroboration. Conclusie: start met flat weights, meet, en tune dan pas.

## Scope

Deze directory bevat beslissingen over het **gehele Klai-platform** — niet website-specifiek, niet repo-specifiek.
Project-specifieke architectuurdocumentatie leeft in het eigen `docs/` van dat project.
