# Acceptance Criteria: SPEC-KB-IMAGE-001

## Scenario 1: GitHub Markdown met Embedded Images

**Given** een GitHub repository met een markdown bestand dat 3 inline images bevat (`![diagram](images/arch.png)`)
**When** de GitHub connector dit bestand synct
**Then** worden alle 3 images gedownload, geupload naar Garage, en de presigned URLs opgeslagen in Qdrant chunk metadata onder `image_urls`

## Scenario 2: PDF Document met Embedded Images

**Given** een PDF document met 2 embedded afbeeldingen geupload via de GitHub connector
**When** Unstructured.io het document partitioneert
**Then** worden de Image elements geextraheerd, geupload naar Garage, en de URLs opgeslagen in de artifact metadata

## Scenario 3: Notion Pagina met Image Blocks

**Given** een Notion pagina met 4 image blocks (2 external URL, 2 file upload)
**When** de Notion connector deze pagina synct
**Then** worden alle 4 images gedownload (external via URL, file via Notion API), geupload naar Garage, en URLs opgeslagen in metadata

## Scenario 4: Website met Inline Images

**Given** een webpagina met 5 images in de HTML
**When** de Web Crawler deze pagina synct via Crawl4AI
**Then** worden de image URLs geextraheerd uit de markdown output, gedownload, geupload naar Garage, en URLs opgeslagen in metadata

## Scenario 5: Image Download Failure (Graceful Degradation)

**Given** een document met 3 images waarvan 1 URL een 404 teruggeeft
**When** het systeem de images probeert te downloaden
**Then** worden de 2 succesvolle images opgeslagen, wordt de 404 gelogd als warning, en gaat de document-ingestie gewoon door

## Scenario 6: Image Te Groot

**Given** een document met een image van 8 MB
**When** het systeem de image probeert te verwerken
**Then** wordt de image overgeslagen met een warning log en gaat de ingestie door

## Scenario 7: Tenant Isolatie

**Given** org_A en org_B die beide hetzelfde document met images ingesten
**When** org_A retrieval doet
**Then** worden alleen images van org_A teruggegeven, nooit van org_B

## Scenario 8: Retrieval Response met Image URLs

**Given** chunks met `image_urls` in Qdrant payload
**When** de retrieval API deze chunks teruggeeft
**Then** bevat de response `image_urls` per chunk

## Scenario 9: Deduplicatie via Content-Addressed Storage

**Given** twee documenten die dezelfde image bevatten (zelfde bytes)
**When** beide documenten worden geingested
**Then** wordt de image maar 1x opgeslagen in Garage (zelfde SHA256 hash) en delen beide artifacts dezelfde URL

## Scenario 10: Backward Compatibility

**Given** bestaande chunks in Qdrant zonder `image_urls` veld
**When** de retrieval API deze chunks opvraagt
**Then** wordt `image_urls` als `null` teruggegeven en werkt de rest van de response normaal

## Edge Cases

- Document met 0 images: geen image processing, normaal ingest pad
- Document met >20 images: eerste 20 worden verwerkt, rest overgeslagen met log
- SVG image: wordt opgeslagen als-is (geen conversie)
- Relative image URL in markdown: correct geresolved t.o.v. document/pagina pad
- Notion image met expiring URL: gedownload en ge-re-uploaded naar Garage (niet de expiring URL bewaard)

## Quality Gates

- [ ] Alle 3 connectors extraheren images correct
- [ ] Images worden opgeslagen in Garage met tenant-scoped paths
- [ ] image_urls overleeft Procrastinate enrichment pipeline
- [ ] Retrieval API stuurt image_urls mee in response
- [ ] Image failures blokkeren ingest niet
- [ ] Geen cross-tenant image leakage
- [ ] Bestaande chunks zonder images blijven werken
- [ ] Test coverage >= 85% op nieuwe code
