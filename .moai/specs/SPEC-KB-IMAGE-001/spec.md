---
id: SPEC-KB-IMAGE-001
version: "1.0.0"
status: implemented
created: 2026-04-08
updated: 2026-04-08
author: Mark Vletter
priority: high
---

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-04-08 | 1.0.0 | Initial SPEC creation |
| 2026-04-08 | 1.1.0 | Component correcties: Garage v2.2.0, minio SDK, filetype validatie, init-script |
| 2026-04-08 | 1.2.0 | Implemented and deployed: Caddy website mode, env var secrets, E2E verified on production |

---

# SPEC-KB-IMAGE-001: Image Storage in Connector Ingest Pipeline

## Summary

Voeg image-extractie en -opslag toe aan de Klai connector pipeline zodat images uit geingeste documenten bewaard blijven en beschikbaar zijn voor weergave. Images worden opgeslagen in Garage (S3-compatible object storage) met tenant-scoped paths en presigned URLs.

## Motivation

Momenteel worden alle images in documenten weggegooid tijdens ingestie. Alleen tekst wordt bewaard. Dit betekent dat diagrammen, screenshots, foto's en andere visuele content uit knowledge bases verloren gaat. Gebruikers verwachten dat hun geuploadde documenten compleet blijven — inclusief images.

## Scope

**In scope:**
- Image extractie uit alle 3 connectors (GitHub, Notion, Web Crawler)
- Image extractie uit PDF/DOCX via Unstructured.io Image elements
- Garage S3 object storage deployment en configuratie
- Image upload naar Garage met tenant-scoped, content-addressed paths
- Image URL metadata in Qdrant chunks en PostgreSQL artifacts
- Image URLs meesturen in retrieval API responses

**Buiten scope (toekomstig werk):**
- Vision LLM captions genereren bij ingestie
- Multimodal embeddings (CLIP/Voyage)
- Image-to-image similarity search
- Frontend rendering van images in chat UI
- Thumbnail generatie

---

## Requirements

### Module 1: Infrastructure — Garage Object Storage

**R1 [Ubiquitous]:**
Het systeem **zal** Garage v2.2.0 (`dxflrs/garage:v2.2.0`) deployen als Docker container in de Klai stack met single-node configuratie (`replication_factor = 1`).

**R1.0.1 [Ubiquitous]:**
Het systeem **zal** een init-script bevatten dat bij eerste start de Garage layout assignt en de image bucket aanmaakt (Garage vereist handmatige bootstrap via CLI).

**R1.1 [Ubiquitous]:**
Het systeem **zal** een async S3 client utility bieden gebaseerd op de `minio` Python SDK met `asyncio.to_thread()`, bruikbaar door zowel klai-connector als klai-knowledge-ingest.

**R1.2 [Ubiquitous]:**
Het systeem **zal** images opslaan met tenant-scoped, content-addressed S3 paths: `/{org_id}/images/{kb_slug}/{sha256}.{ext}`.

**R1.3 [Ubiquitous]:**
Het systeem **zal** presigned URLs genereren voor image-toegang met een configureerbare TTL. De S3 client **zal** `region="garage"` gebruiken (vereist door Garage voor correcte signature validatie).

**R1.4 [Ubiquitous]:**
Het systeem **zal** geuploadde images valideren op bestandstype via magic bytes check (`filetype` library) voordat ze worden opgeslagen. Alleen JPEG, PNG, GIF, WebP en SVG worden geaccepteerd.

### Module 2: Pipeline Plumbing

**R2 [Ubiquitous]:**
Het systeem **zal** `image_urls` ondersteunen als optioneel veld in de ingest pipeline van connector tot retrieval.

**R2.1 [Ubiquitous]:**
`DocumentRef` in `base.py` **zal** een `images: list[ImageRef] | None` veld bevatten met per image: `url`, `alt`, `source_path`.

**R2.2 [Ubiquitous]:**
`IngestRequest` in knowledge-ingest **zal** een optioneel `image_urls: list[str] | None` veld accepteren.

**R2.3 [Ubiquitous]:**
De ingest route **zal** `image_urls` opnemen in `extra_payload` VOOR de Procrastinate `defer_async()` call, zodat enrichment het veld bewaart.

**R2.4 [Ubiquitous]:**
De retrieval API **zal** `image_urls` meesturen in chunk responses wanneer aanwezig in Qdrant payload.

### Module 3: GitHub Connector Image Extractie

**R3 [Event-driven]:**
**Wanneer** de GitHub connector een document synct, **zal** het systeem images extraheren op twee manieren:

**R3.1 [Event-driven]:**
**Wanneer** een markdown bestand `![alt](path)` references bevat, **zal** het systeem de image-URLs extraheren en de image-bestanden ophalen via de GitHub API.

**R3.2 [Event-driven]:**
**Wanneer** Unstructured.io `Image` elements teruggeeft bij PDF/DOCX partitioning, **zal** het systeem de image-data extraheren.

### Module 4: Notion Connector Image Extractie

**R4 [Event-driven]:**
**Wanneer** de Notion connector een pagina synct, **zal** het systeem image blocks herkennen en de image URLs of bestanden downloaden.

**R4.1 [Event-driven]:**
**Wanneer** een Notion image block type `external` heeft, **zal** het systeem de external URL downloaden.

**R4.2 [Event-driven]:**
**Wanneer** een Notion image block type `file` heeft, **zal** het systeem het bestand ophalen via de Notion API URL (die een expiring URL is).

### Module 5: Web Crawler Image Extractie

**R5 [Event-driven]:**
**Wanneer** de Web Crawler connector een pagina synct, **zal** het systeem `![alt](url)` patronen uit de Crawl4AI markdown extraheren en de images downloaden.

**R5.1 [State-driven]:**
**Zolang** een image URL relatief is, **zal** het systeem de URL resolven ten opzichte van de pagina-URL.

### Module 6: Resilience

**R6 [State-driven]:**
**Zolang** een individuele image download faalt (timeout, 404, te groot), **zal** het systeem de fout loggen en de document-ingestie voortzetten zonder die image.

**R6.1 [Ubiquitous]:**
Het systeem **zal** images groter dan 5 MB overslaan met een warning log.

**R6.2 [Ubiquitous]:**
Het systeem **zal** maximaal 20 images per document verwerken.

---

## Technical Constraints

- **Model policy:** Geen US cloud provider APIs — Garage is self-hosted EU storage
- **Backward compatibility:** Bestaande chunks zonder `image_urls` blijven werken (veld is optioneel)
- **Procrastinate safety:** `image_urls` MOET in `extra_payload` voor `defer_async()` — anders wordt het veld verwijderd door de enrichment worker
- **Tenant isolation:** S3 paths MOETEN org_id bevatten; images mogen NOOIT cross-tenant lekken
- **Image formats:** JPEG, PNG, GIF, WebP, SVG — gevalideerd via magic bytes (`filetype` library), niet via extensie
- **Images worden opgeslagen in origineel formaat** — geen WebP conversie of resizing in deze fase
- **Garage licentie:** AGPL v3 — Klai distribueert als apart Docker image, geen code-wijzigingen, geen licentie-conflict met MIT
- **Garage region:** S3 client MOET `region="garage"` configureren, anders falen auth signatures
- **Garage bootstrap:** Vereist post-start CLI interactie (layout assign + bucket create) via init-script
- **S3 client:** `minio` SDK (sync) met `asyncio.to_thread()` — consistent met codebase die httpx gebruikt (geen aiohttp dependency)
