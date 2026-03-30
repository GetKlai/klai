# SPEC-AUDIO-SUMMARY: Detailpagina en AI Samenvatting voor Audio-opnames

**SPEC ID:** SPEC-AUDIO-SUMMARY
**Status:** Draft
**Priority:** Medium
**Created:** 2026-03-24
**Updated:** 2026-03-24 (v2 — holistische herziening na codebase-analyse)

---

## Environment

- **Platform:** Klai Portal (SaaS transcriptie)
- **Scribe service:** FastAPI at `klai-mono/klai-scribe/scribe-api/`
- **Frontend:** TanStack Router + React at `klai-mono/klai-portal/frontend/`
- **Database:** PostgreSQL, scribe schema, SQLAlchemy 2.0 async, Alembic migrations
- **LLM gateway:** LiteLLM (deployed op core-01, nog te configureren in scribe service settings)
- **i18n:** Paraglide (`messages/nl.json`, `messages/en.json`)
- **Auth:** Zitadel OIDC via bestaande `get_user_id` dependency in scribe service

---

## Achtergrond en probleemstelling

De transcriptielijst (`/app/transcribe`) toont zowel audio-opnames (source: `upload`) als vergaderingen (source: `meeting`). Vergaderingen zijn klikbaar en hebben een detailpagina (`/app/meetings/$meetingId`) met transcript, samenvatting, en acties. Audio-opnames zijn **niet klikbaar** — er is geen detailpagina. Alle interactie verloopt via kleine icoontjes in de lijstrij (kopiëren, downloaden, verwijderen, naam bewerken).

Dit SPEC lost twee samenhangende problemen op:

1. **Detailpagina voor audio-opnames** — Vergelijkbaar met `meetings/$meetingId.tsx`, zodat audio-opnames ook aanklikbaar zijn en een volwaardige weergave hebben.
2. **AI samenvatting voor audio-opnames** — Gebruiker kiest type opname (vergadering of algemene opname) en ontvangt een passende AI-samenvatting. Leeft op de detailpagina, niet in een modal.

---

## Aannames

- A-1: De scribe service heeft nog geen LiteLLM-configuratie. Drie settings worden toegevoegd: `litellm_base_url`, `litellm_master_key`, `summarize_model`.
- A-2: `summary_json` bestaat nog niet op `scribe.transcriptions`. Meest recente migratie is `0003`. Nieuwe migratie wordt `0004`.
- A-3: De `GET /klai-scribe/v1/transcriptions/{id}` endpoint bestaat al. De response hoeft alleen `summary_json` erbij te krijgen.
- A-4: Voor de lijstweergave is een `has_summary: bool` veld voldoende — de volledige `summary_json` JSONB hoeft niet in elke lijstrij mee.
- A-5: Audio-opnames hebben geen sprekerssegmenten (`transcript_segments`). De transcripttekst is altijd platte tekst.
- A-6: Een transcriptietekst past in een enkel LLM-contextvenster (< 100K tokens). Chunking is niet nodig voor de initiële implementatie.
- A-7: De tweefasige aanpak (extract → synthesize) uit `klai-portal/backend/app/services/summarizer.py` is het referentiepatroon. De scribe service implementeert dit zelfstandig (geen cross-service dependency).
- A-8: Het type opname (`meeting` of `recording`) wordt opgeslagen in `summary_json.type` — geen aparte kolom nodig.

---

## Requirements

### Deel 1: Detailpagina

**REQ-DETAIL-001 (Event-driven):**
WANNEER een gebruiker op een audio-opname in de lijst klikt, DAN navigeert het systeem naar `/app/transcribe/$transcriptionId`.

**REQ-DETAIL-002 (State-driven):**
De detailpagina toont: de naam/titel van de opname, de transcripttekst, knoppen voor kopiëren en downloaden van de transcriptie, en een terugknop naar de lijst.

**REQ-DETAIL-003 (Ubiquitous):**
De titels van audio-opnames in `index.tsx` zijn klikbaar (net als vergaderingen met status `done`), en navigeren naar de detailpagina.

### Deel 2: Type selectie en samenvatting

**REQ-SUM-001 (State-driven):**
ALS een audio-opname een transcript heeft, DAN toont de detailpagina een type-dropdown en een "Samenvatten" knop.

**REQ-SUM-002 (Ubiquitous):**
De type-dropdown biedt twee opties: "Vergadering" (`meeting`) en "Algemene opname" (`recording`). Standaard geselecteerd: "Algemene opname".

**REQ-SUM-003 (Event-driven):**
WANNEER de gebruiker op "Samenvatten" klikt, DAN roept de frontend `POST /klai-scribe/v1/transcriptions/{id}/summarize` aan met het gekozen type en de taal van de transcriptie.

**REQ-SUM-004 (State-driven):**
ALS `recording_type = "meeting"`, DAN gebruikt het systeem de meeting-promptset (sprekers, besluiten, actiepunten, open vragen, volgende stappen).

**REQ-SUM-005 (State-driven):**
ALS `recording_type = "recording"`, DAN gebruikt het systeem de audio-opname-promptset (kernonderwerpen, kernpunten, conclusies, opvallende uitspraken).

**REQ-SUM-006 (Event-driven):**
WANNEER samenvatting succesvol is, DAN slaat het systeem het resultaat op in `summary_json` en toont de detailpagina de samenvatting als Markdown in een card onder het transcript.

**REQ-SUM-007 (State-driven):**
ALS er al een samenvatting bestaat, DAN toont de detailpagina de bestaande samenvatting plus de mogelijkheid om opnieuw samen te vatten (type opnieuw kiezen via dropdown).

**REQ-SUM-008 (Unwanted):**
Het systeem genereert geen samenvatting voor een transcriptie met lege tekst. Endpoint retourneert HTTP 422.

**REQ-SUM-009 (Unwanted):**
Het systeem hergenereert een bestaande samenvatting niet zonder expliciete `force=true` queryparameter.

**REQ-SUM-010 (State-driven):**
ALS de LiteLLM-aanroep mislukt, DAN retourneert het endpoint HTTP 502 en slaat geen gedeeltelijke samenvatting op. De UI toont een foutmelding.

### Deel 3: Lijst-updates

**REQ-LIST-001 (State-driven):**
ALS een transcriptie een samenvatting heeft (`has_summary: true`), DAN toont de lijstrij een visuele indicator (icoon of badge).

**REQ-LIST-002 (Ubiquitous):**
De acties in de lijstrij (rename, kopiëren, downloaden, verwijderen) blijven beschikbaar naast de klikbare titel.

### Deel 4: i18n

**REQ-I18N-001 (Ubiquitous):**
Alle nieuwe UI-strings zijn beschikbaar in `nl.json` en `en.json`.

---

## Prompts

### Meeting-promptset (type: "meeting")

Identiek aan het patroon in `klai-portal/backend/app/services/summarizer.py`.

**Extractie (system prompt):**
```
You are a precise meeting analyst. Extract factual information from the meeting transcript.
Return ONLY valid JSON with this exact structure:
{
  "speakers_present": ["name1", "name2"],
  "topics": ["topic1", "topic2"],
  "decisions": ["decision1"],
  "action_items": [{"owner": "name or null", "task": "description"}],
  "open_questions": ["question1"],
  "next_steps": ["step1"]
}
Do not add commentary. If a field has no data, use an empty array.
```

**Synthese (system prompt):**
```
You are a professional meeting summarizer. Write a clear, concise meeting summary
based on the extracted facts provided. Use the language specified. Structure:
1. A short executive summary paragraph (2-3 sentences).
2. ## Decisions (if any)
3. ## Action Items (if any, with owner)
4. ## Open Questions (if any)
5. ## Next Steps (if any)
Adapt section headings to the target language. Omit sections with no content.
```

**Gestructureerde output:**
```json
{
  "type": "meeting",
  "markdown": "...",
  "structured": {
    "speakers": [],
    "topics": [],
    "decisions": [],
    "action_items": [{"owner": null, "task": "..."}],
    "open_questions": [],
    "next_steps": []
  }
}
```

### Audio-opname-promptset (type: "recording")

**Extractie (system prompt):**
```
You are a precise content analyst. Extract factual information from this audio transcript.
Return ONLY valid JSON with this exact structure:
{
  "topics": ["topic1", "topic2"],
  "key_points": ["point1", "point2"],
  "quotes": ["memorable quote 1"],
  "conclusions": ["conclusion1"]
}
Do not add commentary. If a field has no data, use an empty array.
Quotes should be exact phrases from the transcript worth highlighting.
```

**Synthese (system prompt):**
```
You are a professional content summarizer. Write a clear, concise summary
based on the extracted information provided. Use the language specified. Structure:
1. A short summary paragraph (2-3 sentences).
2. ## Key Points (if any)
3. ## Conclusions (if any)
4. ## Notable Quotes (if any)
Adapt section headings to the target language. Omit sections with no content.
```

**Gestructureerde output:**
```json
{
  "type": "recording",
  "markdown": "...",
  "structured": {
    "topics": [],
    "key_points": [],
    "quotes": [],
    "conclusions": []
  }
}
```

---

## Specificaties

### Backend (scribe service)

**S-1: DB migratie `0004_add_summary_to_transcriptions.py`**
Voeg één nullable kolom toe aan `scribe.transcriptions`:
- `summary_json JSONB` — nullable (NULL = nooit samengevat; `summary_json.type` bevat het opname-type)

**S-2: Model + schema update**
- Voeg `summary_json: dict | None` toe aan het `Transcription` SQLAlchemy model
- Voeg `summary_json: dict | None` toe aan `TranscriptionResponse` (detail endpoint)
- Voeg `has_summary: bool` (computed: `summary_json IS NOT NULL`) toe aan `TranscriptionListItem` (list endpoint)

**S-3: Config uitbreiding**
Voeg toe aan `Settings` in `app/core/config.py`:
```python
litellm_base_url: str = "http://litellm:4000"
litellm_master_key: str = ""
summarize_model: str = "klai-primary"
```

**S-4: Nieuwe summarizer service `app/services/summarizer.py`**
Functies:
- `get_extraction_prompt(recording_type: str) -> str` — retourneert type-specifieke system prompt
- `get_synthesis_prompt(recording_type: str) -> str` — retourneert type-specifieke synthesis prompt
- `extract_facts(transcript: str, recording_type: str, language: str) -> dict` — LiteLLM call, temperature 0.1
- `synthesize_summary(facts: dict, recording_type: str, language: str) -> str` — LiteLLM call, temperature 0.3
- `summarize_transcription(text: str, recording_type: str, language: str) -> dict` — orchestreert beide, retourneert volledig `summary_json` dict

**S-5: Nieuw endpoint in `app/api/transcribe.py`**
```
POST /klai-scribe/v1/transcriptions/{id}/summarize
Query: force: bool = False
Body: { "recording_type": "meeting" | "recording", "language": str | None }
Response: { "summary_json": { "type": ..., "markdown": ..., "structured": ... } }
```
Logica:
1. Haal transcriptie op, valideer eigenaarschap (user_id)
2. HTTP 422 als `text` leeg is
3. HTTP 200 met bestaande `summary_json` als aanwezig en `force=False`
4. Gebruik `language` param of val terug op `transcription.language`
5. Roep `summarizer.summarize_transcription()` aan
6. Sla op in `transcription.summary_json`
7. Retourneer resultaat

### Frontend

**S-6: Nieuwe detailpagina `src/routes/app/transcribe/$transcriptionId.tsx`**

Patroon: vrijwel identiek aan `meetings/$meetingId.tsx`, aangepast voor audio-opnames.

Bevat:
- Fetch van `GET /klai-scribe/v1/transcriptions/{id}` met auth-header
- Titel (naam of preview van tekst) + terugknop naar `/app/transcribe`
- **Transcript card**: transcripttekst als `<pre>`-stijl, kopieer- en downloadknoppen
- **Samenvatten-sectie**: type-dropdown + "Samenvatten" knop (zichtbaar als transcript bestaat)
  - Dropdown standaard: "Algemene opname"; opties: "Vergadering" / "Algemene opname"
  - Laadspinner tijdens API-aanroep
  - Als `summary_json` bestaat: dropdown toont huidig type, knop tekst: "Opnieuw samenvatten"
- **Summary card**: Markdown gerenderd via `react-markdown` (zoals in meetings), kopieer als tekst + kopieer als Markdown

**S-7: Lijst-aanpassing `src/routes/app/transcribe/index.tsx`**

Twee wijzigingen:
1. Titels van audio-opnames (`source === 'upload'`) worden klikbaar, navigeren naar `/app/transcribe/$transcriptionId` — zelfde patroon als meetings (regels 455–464 nu uitgebreid)
2. Visuele indicator (klein `FileText`-icoon of badge) in de titelskolom als `item.has_summary` true is

Voeg `has_summary?: boolean` toe aan `TranscriptionItem` interface.
Voeg `has_summary` toe aan `toUnified()` helper → `UnifiedItem`.

**S-8: i18n keys**
Toevoegen aan `nl.json` en `en.json`:

| Key | NL | EN |
|-----|----|----|
| `app_transcribe_detail_back` | "Terug naar overzicht" | "Back to overview" |
| `app_transcribe_detail_transcript_title` | "Transcriptie" | "Transcript" |
| `app_transcribe_detail_summary_title` | "Samenvatting" | "Summary" |
| `app_transcribe_summary_type_label` | "Type opname" | "Recording type" |
| `app_transcribe_summary_type_meeting` | "Vergadering" | "Meeting" |
| `app_transcribe_summary_type_recording` | "Algemene opname" | "General recording" |
| `app_transcribe_summarize_button` | "Samenvatten" | "Summarize" |
| `app_transcribe_resummarize_button` | "Opnieuw samenvatten" | "Re-summarize" |
| `app_transcribe_summary_loading` | "Samenvatting genereren..." | "Generating summary..." |
| `app_transcribe_summary_error` | "Samenvatting mislukt" | "Summary failed" |
| `app_transcribe_summary_copy_text` | "Kopieer als tekst" | "Copy as text" |
| `app_transcribe_summary_copy_markdown` | "Kopieer als Markdown" | "Copy as Markdown" |
| `app_transcribe_summary_copy_done` | "Gekopieerd" | "Copied" |
| `app_transcribe_has_summary` | "Heeft samenvatting" | "Has summary" |

---

## Betrokken bestanden

| Bestand | Wijziging |
|---------|-----------|
| `klai-scribe/scribe-api/alembic/versions/0004_add_summary_to_transcriptions.py` | Nieuw: `summary_json JSONB nullable` |
| `klai-scribe/scribe-api/app/models/transcription.py` | Voeg `summary_json` kolom + Pydantic velden toe |
| `klai-scribe/scribe-api/app/core/config.py` | Voeg LiteLLM settings toe |
| `klai-scribe/scribe-api/app/services/summarizer.py` | Nieuw: tweefasen-samenvatting met twee promptsets |
| `klai-scribe/scribe-api/app/api/transcribe.py` | Voeg `POST /{id}/summarize` toe; voeg `has_summary` toe aan list response |
| `klai-portal/frontend/src/routes/app/transcribe/$transcriptionId.tsx` | Nieuw: detailpagina |
| `klai-portal/frontend/src/routes/app/transcribe/index.tsx` | Klikbare titels + `has_summary` indicator |
| `klai-portal/frontend/messages/nl.json` | Nieuwe i18n-keys |
| `klai-portal/frontend/messages/en.json` | Nieuwe i18n-keys |

---

## Buiten scope

- Detailpagina voor vergaderingen aanpassen (is al volledig)
- Automatische type-detectie via AI
- Sub-typen van audio-opnames (interview, lezing, etc.)
- Samenvatting automatisch genereren direct na transcriptie

---

## Deployment checklist

1. **Alembic migratie** (backward compatible — nullable kolom):
   ```bash
   alembic upgrade head
   ```
2. **Environment variables** in `/opt/klai/.env` (scribe service):
   ```
   LITELLM_BASE_URL=http://litellm:4000
   LITELLM_MASTER_KEY=<bestaande master key>
   SUMMARIZE_MODEL=klai-primary
   ```
3. Scribe service herstarten (container restart pre-flight vereist)
4. Frontend deployen
