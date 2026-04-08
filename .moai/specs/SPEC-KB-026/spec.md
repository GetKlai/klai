---
id: SPEC-KB-026
version: "1.1.0"
status: approved
created: "2026-04-06"
updated: "2026-04-06"
author: Mark Vletter
priority: high
tags: [taxonomy, hardening, gap-classification, clustering, auto-categorise]
related: [SPEC-KB-021, SPEC-KB-022, SPEC-KB-023, SPEC-KB-024, SPEC-KB-027]
---

# SPEC-KB-026: Taxonomy Integration Hardening

## HISTORY

| Versie | Datum | Auteur | Wijziging |
|---|---|---|---|
| 1.0.0 | 2026-04-06 | Mark Vletter | Initiele versie na holistische code review KB-021 t/m KB-025 |
| 1.1.0 | 2026-04-06 | Mark Vletter | Tag governance (R7+R8) verplaatst naar SPEC-KB-027; R5 Procrastinate bevestigd aanwezig in portal |

---

## Context

Na de implementatie van KB-021 t/m KB-025 is een holistische code review uitgevoerd. De architectuur is solide en de happy path werkt, maar een aantal integratiepunten zijn onafgemaakt, fout gekoppeld, of bevatten stille failures.

De meest kritieke bug: `clustering_tasks.py` roept `submit_taxonomy_proposal()` aan met een verkeerde functie-signature. Dit veroorzaakt een `TypeError` bij elke clustering run — **de clustering job heeft nog nooit een voorstel ingediend**. Als gevolg hiervan vuurt auto-categorise ook nooit, want het centroid bereikt de portal nooit.

Dit SPEC richt de zes gevonden problemen op prioriteitsvolgorde. Tag governance (KB-022 R5) is bewust buiten scope gehouden en komt in SPEC-KB-027.

---

## Scope

**In scope:**
- `klai-knowledge-ingest`: fix `submit_taxonomy_proposal` signature mismatch in `clustering_tasks.py`
- `klai-knowledge-ingest`: voeg `cluster_centroid` toe aan `TaxonomyProposal` + `portal_client.py`
- `klai-knowledge-ingest`: genereer description in `maybe_generate_proposal()`
- `klai-knowledge-ingest`: voeg max-age check toe aan `load_centroids()`
- `klai-knowledge-ingest`: nieuw intern classify endpoint voor gap classificatie
- `klai-portal/backend`: implementeer gap taxonomy classificatie (stub afmaken in `internal.py`)
- `klai-portal/backend`: maak auto-categorise betrouwbaar via Procrastinate

**Buiten scope:**
- Tag governance (→ SPEC-KB-027)
- Clustering merge/split UI
- Cross-KB taxonomy coherentie
- Float precision optimalisatie van centroid opslag

---

## Requirements

### R1 — Fix: `submit_taxonomy_proposal` API mismatch (KRITIEK — runtime crash)

WHEN de clustering job (`clustering_tasks.py`) een voorstel indient,
THEN SHALL de aanroep naar `submit_taxonomy_proposal` de correcte signature gebruiken van `portal_client.py`.

**Huidig probleem:**
`clustering_tasks.py` roept `submit_taxonomy_proposal()` aan met keyword arguments (`proposal_type`, `title`, `description`, `payload`) die niet bestaan in de functie-signature van `portal_client.py` (die accepteert alleen `(kb_slug, org_id, proposal: TaxonomyProposal)`).
Dit veroorzaakt een `TypeError` bij iedere clustering run — **de clustering job submit nooit proposals**.

**Fix:** pas `clustering_tasks.py` aan zodat het een `TaxonomyProposal` object aanmaakt en dat meegeeft. De `portal_client` API blijft ongewijzigd — die wordt al correct gebruikt door `proposal_generator.py`.

Schrijf een unit test die bewijst dat de `clustering_tasks.py` → `portal_client.py` koppeling werkt zonder `TypeError`.

### R2 — Fix: `cluster_centroid` ontbreekt in proposal payload (KRITIEK — auto-categorise vuurt nooit)

WHEN de clustering job een nieuw cluster proposal indient,
THEN SHALL `cluster_centroid: list[float]` aanwezig zijn in het `payload` dict van het proposal.

**Huidig probleem:**
`portal_client.py`'s `TaxonomyProposal` dataclass heeft geen `cluster_centroid` veld.
`portal/api/taxonomy.py` leest `payload.get("cluster_centroid")` bij approval — dit is altijd `None` → auto-categorise vuurt nooit.

**Oplossing:**
1. Voeg `cluster_centroid: list[float] | None = None` toe aan `TaxonomyProposal` dataclass
2. Pas `submit_taxonomy_proposal` aan zodat `cluster_centroid` in de `payload` dict wordt meegezonden:
   ```json
   {
     "proposal_type": "new_node",
     "title": "<name>",
     "payload": {
       "suggested_name": "<name>",
       "document_count": N,
       "sample_titles": [...],
       "description": "<desc>",
       "cluster_centroid": [0.123, ...]
     }
   }
   ```
3. `clustering_tasks.py` vult `proposal.cluster_centroid` vanuit `cluster.centroid`

WHEN het cluster geen centroid heeft (bijv. bij `maybe_generate_proposal` vanuit reguliere ingest),
THEN SHALL `cluster_centroid` `null` zijn in de payload — auto-categorise wordt dan overgeslagen.

R1 en R2 worden samen geïmplementeerd in één PR.

### R3 — Fix: descriptions ontbreken bij incrementele proposals (MAJOR — classificatiekwaliteit)

WHEN `maybe_generate_proposal()` een voorstel indient vanuit reguliere ingest (niet bootstrap),
THEN SHALL het systeem `generate_node_description()` aanroepen voor de suggested_name
AND de gegenereerde description opslaan in `proposal.description`.

**Huidig probleem:**
`generate_bootstrap_proposals()` genereert descriptions correct.
`maybe_generate_proposal()` maakt een `TaxonomyProposal` aan zonder `generate_node_description()` aan te roepen — description blijft altijd `""`. Oversight.

**Fix:** voeg één `await generate_node_description(suggested_name, None, sample_titles)` aanroep toe in `maybe_generate_proposal()` na het bepalen van `suggested_name`. Gebruik dezelfde error-handling als `generate_bootstrap_proposals()` (exception → `""`).

### R4 — Fix: gap taxonomy classificatie niet gekoppeld (MAJOR — KB-022 R6 onafgemaakt)

**Huidig probleem:**
`internal.py` → `create_gap_event()` heeft een skeleton voor async gap classificatie (regels 553–594), maar logt `"gap_classification_skipped: reason=async classification not yet connected to ingest service"` en doet verder niks.

WHEN een gap event binnenkomt zonder `taxonomy_node_ids`
AND de gap heeft een `nearest_kb_slug`,
THEN SHALL het portal de gap query asynchroon classificeren via een nieuw intern endpoint in knowledge-ingest
AND het resultaat (`taxonomy_node_ids: list[int]`) opslaan op de gap record.

Het nieuwe classify endpoint (`POST /ingest/v1/taxonomy/classify`) ontvangt:
```json
{"org_id": "<str>", "kb_slug": "<str>", "text": "<gap query>"}
```
En retourneert:
```json
{"taxonomy_node_ids": [5, 7]}
```

Het endpoint gebruikt de bestaande `classify_document()` functie uit `taxonomy_classifier.py`.

De classificatie is best-effort: timeout of service unavailable → gap opgeslagen zonder `taxonomy_node_ids`. De gap-opslag wordt nooit geblokkeerd.

Na succesvolle classificatie: `UPDATE portal_retrieval_gaps SET taxonomy_node_ids = ? WHERE id = ?`.

### R5 — Fix: auto-categorise fire-and-forget vervangen door Procrastinate job (MAJOR — stille failures)

**Huidig probleem:**
`portal/api/taxonomy.py` gebruikt `asyncio.create_task()` voor de auto-categorise call naar knowledge-ingest. Bij herstart of tijdelijke unavailability van knowledge-ingest wordt de categorisatie stilletjes gemist — geen retry, geen foutmelding.

WHEN een taxonomy node wordt goedgekeurd (proposal approved),
THEN SHALL het portal een Procrastinate background job inplannen voor auto-categorise.

Het Procrastinate job ontvangt: `org_id`, `kb_slug`, `node_id`, `cluster_centroid`.

WHEN de knowledge-ingest service niet bereikbaar is,
THEN SHALL Procrastinate de job maximaal 3 keer herproberen met exponential backoff (30s, 5m, 30m).

WHEN alle retries mislukken,
THEN SHALL `log.error("auto_categorise_exhausted", ...)` worden gelogd.

Na goedkeuring toont de portal een melding: "Categorie aangemaakt, documenten worden op de achtergrond gecategoriseerd."

### R6 — Fix: centroid store max-age check ontbreekt (MEDIUM — stille classificatiefout)

WHEN `load_centroids()` wordt aangeroepen,
THEN SHALL het systeem controleren of `computed_at` in het centroid bestand niet ouder is dan `TAXONOMY_CENTROID_MAX_AGE_HOURS` (default: 48u).

IF het bestand ouder is,
THEN SHALL `load_centroids()` `None` retourneren (LLM-classificatie wordt gebruikt)
AND `log.warning("centroid_store_stale", age_hours=..., path=...)` worden gelogd.

Rationale voor 48u: de clustering job draait elke 24u — dit geeft één marge-dag voordat het een zichtbaar probleem wordt.

Voeg `taxonomy_centroid_max_age_hours: int = 48` toe aan `config.py`.

---

## Data Model Wijzigingen

### `TaxonomyProposal` dataclass (klai-knowledge-ingest)

```python
@dataclass
class TaxonomyProposal:
    proposal_type: str                              # "new_node"
    suggested_name: str
    document_count: int
    sample_titles: list[str]
    description: str = ""
    cluster_centroid: list[float] | None = None     # NIEUW (R2)
```

### `portal_client.submit_taxonomy_proposal` payload

```json
{
  "proposal_type": "new_node",
  "title": "<name>",
  "payload": {
    "suggested_name": "<name>",
    "document_count": N,
    "sample_titles": [...],
    "description": "<desc>",
    "cluster_centroid": [<float>, ...] | null
  }
}
```

### `config.py` (klai-knowledge-ingest) — nieuwe env var

```python
taxonomy_centroid_max_age_hours: int = 48
```

### Nieuw classify endpoint (klai-knowledge-ingest)

`POST /ingest/v1/taxonomy/classify`

Request: `{"org_id": "<str>", "kb_slug": "<str>", "text": "<str>"}`
Response: `{"taxonomy_node_ids": [<int>, ...]}`

---

## Volgorde van implementatie

| Stap | Requirements | Reden |
|------|-------------|-------|
| 1 | R1 + R2 | Hangen samen; samen één PR |
| 2 | R3 | Klein, één file, onafhankelijk |
| 3 | R4 | Nieuw endpoint + portal wiring |
| 4 | R5 | Portal backend, Procrastinate job |
| 5 | R6 | Klein, één file, onafhankelijk |

---

## Acceptatiecriteria

| # | Criterium | Verificatie |
|---|-----------|-------------|
| AC1 | `clustering_tasks.py` gooit geen `TypeError` bij proposal submission | Unit test: mock `submit_taxonomy_proposal`, verify call arguments matchen `TaxonomyProposal` signature |
| AC2 | `cluster_centroid` is aanwezig in portal na clustering run | Check DB: `SELECT payload->>'cluster_centroid' FROM taxonomy_proposals WHERE proposal_type='new_node' LIMIT 5` |
| AC3 | Auto-categorise vuurt na approval van een cluster-proposal | Approve voorstel, wacht 10s, check Qdrant payloads + logs `auto_categorise_triggered` |
| AC4 | Incrementele proposals hebben een description niet leeg | Trigger `maybe_generate_proposal`, inspect ingediend voorstel in portal |
| AC5 | Gap events zonder taxonomy_node_ids krijgen ze asynchroon | POST gap event zonder taxonomy_node_ids, wacht 5s, check DB: `taxonomy_node_ids IS NOT NULL` |
| AC6 | Classify endpoint retourneert correcte nodes | `POST /ingest/v1/taxonomy/classify` met bekende query, verify geretourneerde node IDs kloppen |
| AC7 | Verouderd centroid bestand wordt niet gebruikt | Zet `computed_at` naar 49u geleden, ingest doc, check logs voor `centroid_store_stale` |
| AC8 | Auto-categorise job retry na tijdelijke failure | Stop knowledge-ingest, keur voorstel goed, herstart service, verify Procrastinate job slaagt alsnog |

---

## Aannames

| Aanname | Confidence | Risico als fout |
|---|---|---|
| `classify_document()` in `taxonomy_classifier.py` is bruikbaar als backend voor classify endpoint | Hoog | Minimale aanpassing nodig |
| Procrastinate is geconfigureerd in portal-api (bevestigd door Mark) | Hoog | Geen fallback nodig |
| Qdrant `set_payload` is safe voor concurrent updates | Hoog | Race conditions bij extreem hoog volume; acceptabel |
| Centroid opslaan als JSONB (~10KB per voorstel) is performant genoeg | Hoog | PostgreSQL comprimeert JSONB; volumes zijn laag |
