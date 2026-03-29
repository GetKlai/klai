# Kennisplatform Pipeline: Extractie, Ontologie en Hybride Retrieval

> Onderzoeksrapport en architectuurbeschrijving
> Datum: 2026-03-18

## Overzicht aanbevolen stack

| Laag | Keuze |
|---|---|
| Extractie | Instructor + Claude Haiku + Anthropic Batches API |
| Vector DB | Qdrant (zelfgehost) |
| Embedding model | BGE-M3 (lokaal) of voyage-3-large (API) |
| Chunking | docling-serve HybridChunker (document/web/notebook); JSON-items direct (helpdesk) |
| Search | Hybrid BM25 + dense, RRF-fusie |
| Reranker | bge-reranker-v2-m3 (open source) |
| Generatie | Claude via LiteLLM, grounded response met bronvermelding |
| Orkestratie | Haystack (retrieval pipeline) + FastAPI (REST API + access policy) |
| Gap-classificatie | Cosine drempel + Claude Haiku als LLM-judge |
| Gap-aggregatie | Qdrant gap-registry + BERTopic clustering |
| Output | Geprioriteerde redactie-inbox (frequentie x urgentie x recency) |

---

## 1. Extractieschema

> **Dit schema is illustratief, geen definitief format.**
>
> De velden, enums en voorbeeldwaarden hieronder zijn gebaseerd op patronen uit vergelijkbare productiesystemen (AWS Post-Call Analytics, Google Agent Assist, Zendesk Contact Lens). Ze zijn een startpunt, geen universele waarheid.
>
> Per tenant is discovery nodig voordat dit schema in productie gaat:
> - Welke `product_area`-categorieën zijn relevant voor deze organisatie?
> - Welke integraties en third-party tools zijn in scope?
> - Welke `problem_category`-enums dekken de werkelijke gesprekstypen?
> - Welke foutcodes zijn herkenbaar en de moeite van extractie waard?
>
> Begin met dit schema als hypothese. Evalueer op 50--100 echte gesprekken. Pas de enums, beschrijvingen en few-shot voorbeelden aan op basis van wat het model consistent en bruikbaar kan extraheren voor deze specifieke tenant.

### Kernvelden voor KB-gap-detectie

De velden met de hoogste signaalwaarde voor downstream semantische similarity (op basis van productiesystemen zoals AWS Post-Call Analytics, Google Agent Assist, Zendesk Contact Lens):

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "HelpdeskGespreksExtractie",
  "type": "object",
  "required": ["problem_summary", "language", "product_area"],
  "properties": {

    "problem_summary": {
      "type": "string",
      "description": "Kernprobleem in 1-2 zinnen. Basis voor semantische matching met helpartikelen."
    },

    "problem_category": {
      "type": "string",
      "enum": ["technical", "account", "billing", "usage", "onboarding", "other"],
      "description": "Coarse-grained categorie voor filtering downstream."
    },

    "product_area": {
      "type": "string",
      "description": "Concreet product of feature. Kritisch voor similarity matching."
    },

    "customer_intent": {
      "type": "string",
      "enum": ["configure", "troubleshoot", "understand", "cancel", "upgrade", "other"],
      "description": "Onderscheidt 'hoe werkt dit' van 'iets kapot'. Intent is discriminatiever dan category."
    },

    "information_sought": {
      "type": "string",
      "description": "Wat zocht de klant -- genormaliseerd als zoekquery. Meest directe input voor similarity."
    },

    "unanswered_questions": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Vragen die de klant stelde die de agent NIET bevredigend kon beantwoorden. Primair signaal voor KB-gaten."
    },

    "agent_uncertainty_indicators": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Exacte zinnen: 'ik weet dat niet', 'ik moet dat uitzoeken', 'laat me even checken'. Proxy voor ontbrekende kennis bij de agent."
    },

    "error_codes": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Exacte foutcodes/meldingen. Hoge signaalwaarde voor gap-detectie."
    },

    "steps_taken": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Stappen die klant en/of agent doorlopen hebben. Detecteert ontbrekende troubleshoot-flows."
    },

    "resolution": {
      "type": "object",
      "properties": {
        "resolved": {"type": "boolean"},
        "resolution_description": {"type": "string"},
        "workaround_used": {"type": "boolean"}
      },
      "description": "Onopgeloste gesprekken = zwaarste kandidaten voor gap-analyse."
    },

    "article_referenced": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Als agent verwees naar artikel/URL: vastleggen. Directe link naar kennisbasis."
    },

    "entities": {
      "type": "object",
      "properties": {
        "integrations": {"type": "array", "items": {"type": "string"}},
        "third_party_tools": {"type": "array", "items": {"type": "string"}},
        "data_formats": {"type": "array", "items": {"type": "string"}}
      },
      "description": "Externe systemen/tools. Helpt integratie-gaten te vinden."
    },

    "knowledge_gap_signal": {
      "type": "string",
      "enum": ["none", "weak", "strong"],
      "description": "LLM-inschatting of dit gesprek op een gap wijst. Heuristiek voor prioritering."
    },

    "language": {
      "type": "string",
      "description": "ISO 639-1 taalcode (nl, en, de, fr, es, pt)."
    },

    "call_metadata": {
      "type": "object",
      "properties": {
        "duration_minutes": {"type": "number"},
        "transcript_quality": {"enum": ["good", "fair", "poor"]}
      }
    },

    "org_id": {
      "type": "string",
      "description": "Organisatie-ID van de tenant. Verplicht voor data-isolatie in Qdrant."
    },

    "visibility": {
      "type": "string",
      "enum": ["internal", "external", "public"],
      "description": "Toegangsbeleid voor dit kennisitem. Default: internal. Wordt doorgegeven aan de ingest API en opgeslagen als payload-veld in Qdrant."
    }
  }
}
```

### Wat weg te laten

Sentiment/CSAT (interessant maar lage signaalwaarde voor gap-analyse), agent-naam, tijdstempels per utterance. Die voegen ruis toe zonder winst voor de downstream taak.

---

## 2. Promptstrategie

### Single prompt vs. chain

**Single prompt** werkt voor dit use case. Een gemiddeld telefoongesprek (15-30 min) = 2.000-5.000 woorden = 4.000-7.000 tokens. Ruim binnen elk contextvenster.

**Een 2-stap chain is zinvol:**
- Pass 1: volledige extractie
- Pass 2 (gleaning): voor `unanswered_questions` bij gesprekken met `knowledge_gap_signal != "none"` **of** `resolution.resolved == false`

Een chain voor alle velden voegt latency en kosten toe zonder winst.

### Zero-shot vs. few-shot

| Veldtype | Aanpak | Reden |
|---|---|---|
| Enums, booleans, taaldetectie | Zero-shot | Voldoende voor gesloten schema |
| `information_sought`, `unanswered_questions` | Few-shot (5-8 voorbeelden) | Open-ended, vereist interpretatie van impliciete inhoud |
| `agent_uncertainty_indicators` | Few-shot met voorbeeldzinnen | Sterk domeinspecifiek taalgebruik |

**Praktisch advies:** start zero-shot, evalueer op 50 gesprekken, voeg few-shot examples toe voor velden met hoge inconsistentie.

Claim-matching onderzoek (Pisarevskaya & Zubiaga, 2025): 10 goed gekozen voorbeelden lieten Gemini-1.5 95% F1 halen vs. 96,2% voor een fine-tuned classifier -- few-shot kan fine-tuned prestaties benaderen.

### Verificatieloop

**Wat niet werkt:** LLM vraagt zichzelf te corrigeren ("is dit correct?"). MIT TACL survey (2024) concludeert dat intrinsieke zelfcorrectie zonder externe feedback onbetrouwbaar is.

**Wat wel werkt:**
1. **Pydantic schema-validatie + automatische retry** (Instructor) -- externe feedback via validatiefout, geen LLM-oordeel
2. **Gleaning pattern**: tweede prompt vraagt "wat ontbreekt er nog?" (niet "klopt dit?")

```python
# Gleaning: aanvulling op unanswered_questions
# Instructietekst in het Engels -- universeel, ongeacht transcript-taal.
# Few-shot voorbeelden (elders gedefinieerd) zijn wel in de doeltaal.
gleaning_prompt = f"""What questions did the customer ask that the agent did NOT satisfactorily answer?
List only NEW ones not already in: {extraction.unanswered_questions}

Transcript: {transcript}"""
```

**Let op de trigger:** Activeer gleaning op `knowledge_gap_signal != "none"` **of** `resolution.resolved == false`. De signaalwaarde wordt bepaald in dezelfde extractie-pass -- als het model een gap mist en ten onrechte `none` geeft, vervalt ook de gleaning. Onopgeloste gesprekken (`resolved == false`) zijn de zwaarste gap-kandidaten en moeten altijd gleaning triggeren, ongeacht het signaal.

Dit verbetert recall op het kritische veld zonder de risico's van zelfcorrectie-loops.

### Meertaligheid

**Kritisch:** vertalen naar Engels vóór extractie verlaagt kwaliteit voor Nederlands. Onderzoek op Nederlandse klinische rapporten (Builtjes et al., JAMIA Open 2025, geëvalueerd op DRAGON-taken) bevestigt: machine-vertaling naar het Engels voor inference verslechtert de prestaties consistent -- native Dutch inference werkt beter.

| Taal | Aandachtspunt |
|---|---|
| Nederlands | Native prompts; Llama-3.3-70B, Claude, Qwen presteren goed |
| Frans | JSON-parseerfouten door accenten (accent aigu/grave); test expliciet; gebruik `ensure_ascii=False` |
| Duits | Goed gedocumenteerd in medische extractie, naar verwachting robuust |
| Spaans/Portugees | Minder getest; valideer op eigen data |

**Schema/enum-waarden** in het Engels houden (genormaliseerde output). **Systeeminstructies** ook in het Engels -- frontier modellen begrijpen instructies taalzelfstandig en het vereenvoudigt het prompt-beheer over talen. **Few-shot voorbeelden** in de doeltaal schrijven -- dit is waar de "native language" winst zit.

---

## 3. Framework vergelijking

| Criterium | Instructor + Claude | Haystack + NuExtract | BAML | LangChain | LlamaIndex |
|---|---|---|---|---|---|
| Structuurbetrouwbaarheid | Hoog (Pydantic + retry) | Hoog (fine-tuned model) | Hoog (token-constraint) | Matig | Matig |
| Meertaligheid | Volledig (via Claude/GPT-4o) | Beperkt (Phi-3 basis) | Volledig (via API) | Afhankelijk van model | Afhankelijk van model |
| Kosten | Per token (API) | Laag (self-hosted 3.8B) | Per token | Per token | Managed cloud |
| Complexiteit | Laag | Medium | Medium (DSL leren) | Hoog | Hoog |
| Debugging | Eenvoudig | Medium | Eenvoudig | Complex | Complex |
| Batch-ondersteuning | Ja (Batches API) | Ja | Ja | Ja | Ja |

### Beoordeling per optie

**Instructor + Pydantic** (aanbeveling voor dit use case)
- 3M+ downloads/maand, meest battle-tested in productie
- Automatische retry op validatiefout
- Werkt met 15+ LLM-providers
- Minimale setup: `pip install instructor`

**NuExtract 2.0 PRO**
- Verslaat GPT-4.1 met +9 F-score op extractiebenchmarks (NuMind's eigen interne benchmark, nog niet publiek gepubliceerd of onafhankelijk gereproduceerd)
- Forceert lege velden bij onzekerheid (geen hallucinaties)
- **Caveat:** meertalige benchmarks voor Nederlands niet gepubliceerd -- valideer op eigen data vóór adoptie

**BAML**
- Contract-first DSL; genereert type-safe clients voor Python/TypeScript/Ruby
- 50-80% minder tokens dan JSON Schema in prompt
- Zinvol als teams groeien of multi-language SDK support nodig is

**LangChain / LlamaIndex**
- Overkill voor extraction-only pipelines
- Abstracties breken regelmatig bij LLM-API-updates
- Zinvol bij complexe RAG-stappen of als al embedded in de bestaande stack

---

## 3.5 GDPR en zelfgehoste alternatieven

Helpdesk-transcripten bevatten PII: namen, e-mailadressen, accountgegevens, telefoonnummers. Verzenden naar de Claude API betekent dat data buiten de EU wordt verwerkt door een derde partij. De AVG vereist een rechtmatige grondslag plus adequaat transfer-mechanisme (Standard Contractual Clauses) voor data naar de VS -- wat operationele overhead en juridisch risico meebrengt.

De schonere oplossing: de extractie-pipeline volledig zelfgehost draaien. Alle data blijft op de eigen server.

### Aanbevolen self-hosted stack

| Model | Grootte | Nederlands | JSON mode | Hardware (FP16) | Licentie |
|---|---|---|---|---|---|
| **Mistral Small 3.1** | 22B | Ja (natief) | Native JSON mode | 1x RTX 4090 (24 GB) of quantized op 16 GB | Apache 2.0 |
| Mistral Large 3 | 123B | Ja | Ja | 4x A100 80 GB | Mistral Research |
| NuExtract 2.0 PRO | ~7B | Niet gebenchmarkt* | Ja (fine-tuned) | 1x A10 | - |
| Llama 3.3 70B | 70B | Niet officieel** | Ja | 2x A100 40 GB | Llama 3.3 |

*NuExtract heeft geen gepubliceerde Nederlandse benchmark -- valideer op eigen data vóór adoptie.
**Meta ondersteunt officieel 8 talen; Nederlands zit er niet bij. Werkt in de praktijk redelijk maar zonder garanties.

**Aanbeveling: Mistral Small 3.1**

Enige model in de lijst dat tegelijkertijd aan alle eisen voldoet: Nederlands natief, JSON mode ingebouwd, past op één GPU, open source licentie. Via Ollama is het direct beschikbaar als drop-in voor de bestaande stack.

### Integratie via Instructor + Ollama

Instructor werkt met elke OpenAI-compatibele endpoint, inclusief Ollama:

```python
import instructor
import openai

# Swap Claude → Mistral via Ollama (zelfde Pydantic modellen hergebruiken)
client = instructor.from_openai(
    openai.OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",  # placeholder, wordt niet gebruikt
    ),
    mode=instructor.Mode.JSON,
)

extraction = client.chat.completions.create(
    model="mistral-small3.1",
    max_tokens=1024,
    messages=[{"role": "user", "content": prompt}],
    response_model=CallExtraction,
    max_retries=3,
)
```

De Pydantic-modellen uit sectie 5 zijn ongewijzigd herbruikbaar. Wissel alleen de `client`-initialisatie.

### Kwaliteitsafweging

Mistral Small 3.1 presteert iets onder Claude Haiku op open-ended velden (`information_sought`, `unanswered_questions`). Op gesloten velden (enums, booleans, taaldetectie) is het verschil verwaarloosbaar.

Aanbevolen aanpak:
1. Kalibreer few-shot voorbeelden op Claude Haiku (betere output = betere voorbeelden)
2. Benchmark Mistral Small 3.1 op dezelfde 50 gesprekken
3. Als quality delta < 5% op de kritische velden: switch naar Mistral voor alle productie-draaiingen

### Hybride aanpak (minimale AVG-blootstelling zonder full self-hosting)

Als self-hosting op korte termijn niet haalbaar is: pseudonimiseer PII vóór verzending naar de API.

**Juridisch kader (EDPB 2024-2025):** pseudonimisering is geen anonimisering. Pseudonieme data blijft persoonsdata onder de AVG (Recital 26 GDPR; EDPB Guidelines 01/2025 expliciet). Een helpdeskgesprek is ook na vervanging van namen en nummers identificeerbaar via gesprekscontext. Pseudonimisering verlaagt het risico maar heft AVG-verplichtingen -- inclusief de eis voor een rechtmatige grondslag en SCC's bij verzending naar de VS -- niet op.

**Waarom regex onvoldoende is voor call transcripts:**
- Namen hebben geen patroon en worden nooit door regex gevonden
- ASR-transcriptie breekt regex-patronen (telefoonnummer gesproken als "nul zes, twee drie...")
- Contextgebonden PII ("mijn bestelling van vorige week dinsdag") is niet structureel detecteerbaar

**Aanbevolen aanpak: Presidio + GLiNER**

Presidio (Microsoft, open source) combineert NER met regex-recognizers. Gebruik `gliner_multi-v2.1` als NER-engine voor Nederlandse naamsdetectie (zero-shot, 100+ talen):

```python
# pip install presidio-analyzer presidio-anonymizer
# python -m spacy download nl_core_news_sm
# pip install gliner

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine

configuration = {
    "nlp_engine_name": "transformers",
    "models": [{"lang_code": "nl", "model_name": {
        "spacy": "nl_core_news_sm",
        "transformers": "urchade/gliner_multi-v2.1"
    }}],
}

provider = NlpEngineProvider(nlp_configuration=configuration)
nlp_engine = provider.create_engine()
analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["nl"])
anonymizer = AnonymizerEngine()

def pseudonymize_transcript(text: str) -> str:
    """Vervang PII door placeholders via NER + regex.
    Let op: resultaat is pseudoniem, niet anoniem -- AVG blijft van toepassing."""
    results = analyzer.analyze(text=text, language="nl")
    return anonymizer.anonymize(text=text, analyzer_results=results).text
```

Voeg Dutch-specifieke recognizers toe voor BSN en IBAN als die in je transcripten voorkomen. Presidio biedt hiervoor een `PatternRecognizer`-interface.

**Beperkingen die blijven gelden:**
- Contextgebonden PII is niet detecteerbaar via NER of regex
- Na pseudonimisering blijft de data AVG-plichtig -- SCC's zijn nog steeds vereist bij verzending naar de VS
- Presidio vereist configuratie voor Nederlands; niet plug-and-play

---

## 4. Lange contextvensters en agentic extractie

### Context rot

Chroma Research testte 18 frontier modellen (inclusief Claude, GPT-4.1, Gemini 2.5) en vond: **elk model degradeert naarmate de contextlengte toeneemt**, maar het patroon is continu, niet-uniform en sterk taakafhankelijk. Er is geen vaste drempelwaarde; de mate van degradatie verschilt per model en taak.

Drie oorzaken:
1. Lost-in-the-middle: 30%+ accuraatheidsval bij relevante content in het midden van een lange context
2. Attention dilution: kwadratische kosten van pairwise attention op schaal
3. Distractor interference: semantisch vergelijkbare maar irrelevante content in een lange batch misleidt het model

**Conclusie voor batching:** meerdere transcripten in één contextvenster stoppen is onbetrouwbaar. Gebruik één API-call per transcript.

### Wanneer is lange context zinvol?

- Cross-call patroondetectie (welke problemen komen het meest voor over 50+ gesprekken)
- Volledige kennisbasis in context laden voor directe vergelijking (vervangt dan de similarity-stap)

### Agentic extractie: wanneer de moeite waard?

| Situatie | Aanpak |
|---|---|
| Standaard batch, vaste schema | Single call per transcript |
| Recall is kritisch (`unanswered_questions`) | Gleaning pattern (pass 2) |
| Compliance/medische context | Actor/Critic quality gate |
| Automatisch artikelen draften op basis van gap | Agentic orchestratie zinvol |

Voor de huidige scope (extractie + gap-detectie) is agentic overkill. Multi-agent architectuur wordt zinvol als de pipeline uitbreidt naar automatisch draften van nieuwe helpartikelen.

---

## 5. Concrete implementatie

### Pipeline

```
Transcript (tekst)
  --> Taaldetectie (langdetect, ~1ms)
  --> Selecteer taalspecifieke few-shot examples
  --> Pass 1: Instructor + Claude Haiku --> CallExtraction (Pydantic, max 3 retries)
  --> Als knowledge_gap_signal != "none":
        Pass 2: Gleaning prompt --> aanvullen unanswered_questions
  --> Output: JSON per gesprek
  --> Downstream RAG-pipeline (zie sectie 6):
        BGE-M3 embedding --> Qdrant hybrid search --> bge-reranker
  --> Gap-detectie (zie sectie 7):
        cosine drempel --> LLM-judge --> gap-registry --> geprioriteerde redactie-inbox
```

### Basiscode

```python
import anthropic
import instructor
from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional

client = instructor.from_anthropic(anthropic.Anthropic())

class Resolution(BaseModel):
    resolved: bool
    resolution_description: Optional[str] = None
    workaround_used: bool = False

class KnowledgeGapSignal(str, Enum):
    none = "none"
    weak = "weak"
    strong = "strong"

class CallExtraction(BaseModel):
    problem_summary: str = Field(description="Core problem in 1-2 sentences")
    product_area: str
    customer_intent: str
    information_sought: str
    unanswered_questions: list[str] = Field(default_factory=list)
    agent_uncertainty_indicators: list[str] = Field(default_factory=list)
    error_codes: list[str] = Field(default_factory=list)
    steps_taken: list[str] = Field(default_factory=list)
    resolution: Resolution
    knowledge_gap_signal: KnowledgeGapSignal
    language: str = Field(description="ISO 639-1 language code")

class AdditionalQuestions(BaseModel):
    questions: list[str]

def extract_call(transcript: str, few_shot_examples: str = "") -> CallExtraction:
    extraction = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": few_shot_examples,
                    "cache_control": {"type": "ephemeral"}  # Prompt caching
                },
                {
                    "type": "text",
                    "text": f"Extract structured information from this helpdesk transcript:\n\n{transcript}"
                }
            ]
        }],
        response_model=CallExtraction,
        max_retries=3,
    )

    # Gleaning pass voor kritische velden.
    # Dubbele trigger: signaal uit extractie-pass OF onopgelost gesprek.
    # Zonder de tweede conditie mist gleaning precies de gevallen waarbij het model
    # de gap in pass 1 over het hoofd zag en knowledge_gap_signal foutief op "none" zette.
    if (extraction.knowledge_gap_signal != KnowledgeGapSignal.none
            or not extraction.resolution.resolved):
        gleaning = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": f"""What questions did the customer ask that the agent did NOT satisfactorily answer?
List only NEW ones not already in: {extraction.unanswered_questions}

Transcript: {transcript}"""
            }],
            response_model=AdditionalQuestions,
            max_retries=2,
        )
        extraction.unanswered_questions.extend(gleaning.questions)

    return extraction
```

### Positie in het Knowledge-platform

Deze pipeline is de **helpdesk-adapter** -- één van meerdere ingestion-adapters in het Knowledge-platform. Elke adapter normaliseert een brontype naar tekst + metadata en levert dat af aan de unified ingest API. Die API verzorgt chunking, BGE-M3 embedding en Qdrant-schrijfoperaties voor alle brontypen -- niet de adapter zelf.

```
PDF, DOCX, XLSX, PPTX
  --> Document-adapter
        --> docling-serve (HTTP, zelfgehost) --> markdown + metadata

Web pages (HTML)
  --> Webcrawl-adapter
        --> Crawl4AI --> markdown (zie sectie 6.0)

Helpdesk transcript (JSON)
  --> Helpdesk-adapter (deze pipeline)
        --> CallExtraction (Pydantic) met org_id + visibility

Research notebooks (markdown)
  --> Notebook-adapter
        --> direct doorsturen, geen parsing nodig

              ↓ alle adapters leveren aan:

Unified Ingest API (zie Appendix B)
  --> chunking (MarkdownHeaderSplitter + RecursiveCharacter)
  --> BGE-M3 embedding (dense + sparse)
  --> Qdrant (org_{org_id}_{content_type}, visibility als payload)
```

**Document-adapter -- al in productie:** `docling-serve` draait al als zelfgehoste HTTP-microservice in `klai-research/research-api`. De service ontvangt files (`/v1/convert/file`) of URLs (`/v1/convert/source`) en geeft gestructureerde markdown terug inclusief tabellen en koppen. Ondersteunde formaten: PDF, DOCX, XLSX, PPTX, images. Zie `klai-research/research-api/app/services/docling.py`.

De volledige unified ingest API-specificatie staat in Appendix B.

### Stappenplan

1. Bouw schema + Pydantic model (inclusief `org_id` en `visibility`)
2. Annoteer 10-20 echte gesprekken als few-shot voorbeelden (per taal)
3. Evalueer zero-shot baseline op 50 gesprekken: welke velden zijn inconsistent?
4. Voeg few-shot examples toe voor zwakke velden
5. Implementeer gleaning pass voor `unanswered_questions`
6. Koppel aan de unified ingest API (niet direct aan Qdrant)
7. Koppel daarna pas aan de similarity-pipeline

---

## 6. RAG-architectuur voor de kennisbasis

### 6.0 Kennisbasis inladen: van sitemap naar Qdrant

290 pagina's × 9 talen = 2.610 publiek beschikbare URL's. De ingest-pipeline verloopt in vier fasen.

#### Is Q&A-conversie nog nodig?

Nee. Het historische argument voor Q&A-conversie was query/document-asymmetrie: een korte vraag en een lang antwoord-document embedden ver uit elkaar. BGE-M3 lost dit op met symmetrische encoding -- query en document worden identiek behandeld.

De 2025-vervanging is **HyPE** (fase 3 hieronder): genereer vragen bij ingest in plaats van bij query-tijd. Dit geeft dezelfde semantische alignment zonder per-query LLM-kosten.

---

#### Fase 1: Crawlen via sitemap

**Aanbeveling: Crawl4AI** (open source, Python-native, zelfgehost)

- Leest sitemap.xml inclusief geneste sitemaps
- `validate_sitemap_lastmod=True` -- slaat ongewijzigde pagina's over bij elke run
- Async/parallel, crash recovery voor lange crawls
- Geen API-kosten

**Hreflang uit de sitemap -- gratis cross-language page graph:**

Multilinguele helpcenter-sitemaps coderen cross-language relaties als `xhtml:link`-elementen. Dit is je gratis `url_map` -- geen extra calls nodig om te weten welke pagina's equivalent zijn over talen.

```python
from xml.etree import ElementTree
import httpx

NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "xhtml": "http://www.w3.org/1999/xhtml",
}

def parse_sitemap(url: str) -> list[dict]:
    root = ElementTree.fromstring(httpx.get(url, timeout=10).content)
    # Recursief: sitemap-index verwijst naar meerdere sitemap-bestanden
    if root.tag.endswith("sitemapindex"):
        pages = []
        for sm in root.findall("sm:sitemap", NS):
            pages.extend(parse_sitemap(sm.findtext("sm:loc", namespaces=NS)))
        return pages
    pages = []
    for u in root.findall("sm:url", NS):
        # Hreflang: {"nl": "https://...", "en": "https://...", ...}
        alternates = {
            link.get("hreflang"): link.get("href")
            for link in u.findall("xhtml:link", NS)
            if link.get("rel") == "alternate"
        }
        pages.append({
            "url": u.findtext("sm:loc", namespaces=NS),
            "lastmod": u.findtext("sm:lastmod", namespaces=NS),
            "url_map": alternates,
        })
    return pages
```

---

#### Fase 2: Contextual Retrieval

**Anthropic Contextual Retrieval** (2024) -- prepend een korte LLM-gegenereerde context-zin vóór elke chunk, voordat je embedt. Dit situeert de chunk binnen zijn pagina.

Waarom dit nodig is voor helpcenter-content: chunks bevatten regelmatig impliciete verwijzingen ("zoals hierboven beschreven", "klik op de knop hieronder") die hun embedding zinloos maken zonder context.

Gemeten effect: 49% minder retrieval-fouten met Contextual Embeddings + BM25; 67% minder mét reranking (Anthropic, 2024).

**Kosten met prompt caching (Claude Haiku):** ~$5 eenmalig voor het volledige corpus (290 × 9 × ~2.000 tokens/pagina = 5,2M tokens). Het document wordt één keer gecached; alle chunks van die pagina hergebruiken de cache.

```python
import anthropic

client = anthropic.Anthropic()

def add_context(document: str, chunk: str) -> str:
    """Prepend een 1-2 zinnen context aan elke chunk voor betere embedding."""
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"<document>{document}</document>",
                    "cache_control": {"type": "ephemeral"},  # Cache per pagina
                },
                {
                    "type": "text",
                    "text": (
                        "Give a short (1-2 sentence) context for this chunk "
                        "that situates it within the document above. "
                        "Answer only with the context, no preamble.\n\n"
                        f"<chunk>{chunk}</chunk>"
                    ),
                },
            ],
        }],
    )
    context = response.content[0].text
    return f"{context}\n\n{chunk}"
```

---

#### Fase 3: Index-time vraaggerniratie (HyPE)

In plaats van de chunk-tekst direct te embedden: genereer 3-5 vragen die de chunk beantwoordt. Embed die *vragen* in Qdrant en sla de chunk als payload op.

Reden: queries zijn vragen; chunks zijn antwoorden. Vraag-naar-vraag-matching in dezelfde semantische ruimte is preciezer dan vraag-naar-document. Geen Q&A-conversie nodig, geen HyDE (dat per query LLM-calls vereist).

Gemeten resultaten (Vake et al., 2025): +42 pp precision, +45 pp recall versus standaard direct embedding.

```python
def generate_questions(chunk_text: str, n: int = 4) -> list[str]:
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": (
                f"Generate {n} different questions that this text answers. "
                "Return only the questions, one per line.\n\n"
                f"Text: {chunk_text}"
            ),
        }],
    )
    return [q.strip() for q in response.content[0].text.strip().split("\n") if q.strip()]

# Bij ingest: embed de vragen, sla de chunk op als payload
enriched_chunk = add_context(full_page_text, raw_chunk)
questions = generate_questions(enriched_chunk)

points = []
for i, question in enumerate(questions):
    embedding = bge_m3.encode(question)  # Embed de vraag, niet de chunk
    points.append(PointStruct(
        id=f"{article_id}_{chunk_index}_{i}",
        vector={"dense": embedding},
        payload={"chunk_text": enriched_chunk, **chunk_metadata},
    ))
```

---

#### Metadata-schema per chunk

```json
{
  "article_id": "orders/cancel-order",
  "lang": "nl",
  "url": "https://help.example.com/nl/orders/bestelling-annuleren",
  "url_map": {
    "nl": "https://help.example.com/nl/orders/bestelling-annuleren",
    "en": "https://help.example.com/en/orders/cancel-order"
  },
  "title": "Bestelling annuleren",
  "nav_path": ["Bestellingen", "Bestelling beheren", "Bestelling annuleren"],
  "nav_path_string": "Bestellingen > Bestelling beheren > Bestelling annuleren",
  "category": "Bestellingen",
  "subcategory": "Bestelling beheren",
  "level": 3,
  "last_updated": "2025-02-15",
  "content_hash": "sha256:...",
  "chunk_index": 2,
  "total_chunks": 5,
  "org_id": "org-uuid",
  "visibility": "internal"
}
```

`article_id` koppelt alle 9 taalversies. Met `group_by="article_id"` in Qdrant haal je alle taalversies van een artikel tegelijk op.

`nav_path` is de sleutel voor gap-plaatsing: als een gap gedetecteerd wordt, embed je het topic, haal je top-K vergelijkbare chunks op, en kijk je welke `nav_path` het vaakst voorkomt. Dat is de aanbevolen plek in de navigatiestructuur.

**Payload-indexes aanmaken direct na collection-creatie** (Qdrant raadt dit sterk aan voor HNSW-optimalisatie):

```python
for field in ["lang", "article_id", "category"]:
    client.create_payload_index(
        collection_name="help_center",
        field_name=field,
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
```

---

#### Incrementele updates

Bij elke run: vergelijk `sitemap lastmod` met lokale registry + content hash. Alleen gewijzigde pagina's opnieuw verwerken.

```python
import hashlib, sqlite3

registry = sqlite3.connect("ingest_registry.db")
registry.execute(
    "CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, content_hash TEXT, lastmod TEXT)"
)

def should_reindex(url: str, lastmod: str, content: str) -> bool:
    new_hash = hashlib.sha256(content.encode()).hexdigest()
    row = registry.execute(
        "SELECT content_hash FROM pages WHERE url=?", (url,)
    ).fetchone()
    if row and row[0] == new_hash:
        return False  # Ongewijzigd, overslaan
    registry.execute(
        "INSERT OR REPLACE INTO pages VALUES (?,?,?)", (url, new_hash, lastmod)
    )
    registry.commit()
    return True

def reindex_page(url: str, new_points: list):
    # Verwijder bestaande chunks voor dit URL
    client.delete(
        collection_name="help_center",
        points_selector=models.FilterSelector(
            filter=models.Filter(must=[
                models.FieldCondition(key="url", match=models.MatchValue(value=url))
            ])
        ),
    )
    client.upsert(collection_name="help_center", points=new_points)
```

---

### 6.1 Vector database

| | pgvector | Qdrant | Weaviate | Chroma |
|---|---|---|---|---|
| Beste voor | Klein, al Postgres | Productie, schaalbaarheid | Native hybride search | Prototyping |
| Hybride search | Beperkt | Ingebouwd | Ingebouwd | Nee |
| Max dimensies | 2.000 | Onbeperkt | Onbeperkt | Onbeperkt |
| Operationele complexiteit | Laag | Medium | Hoog | Laag |
| Schaalbaarheid | Tot ~10M vectors | 1B+ vectors | Hoog | Beperkt |

**Aanbeveling: Qdrant** (zelfgehost via Docker)

Redenen: geschreven in Rust (snelste QPS/dollar bij self-hosting), geavanceerde metadata-filtering, hybrid search (dense + sparse) ingebouwd, BGE-M3 sparse native ondersteund, collection-per-tenant voor structurele GDPR-isolatie. Zie Appendix A voor de volledige vergelijking met Weaviate en pgvector en de motivering voor de afwijzing van shared-collection multi-tenancy.

**Multi-tenancy architectuur: collection-per-tenant**

Eén Qdrant-collectie per organisatie per content-type. Naamgeving: `org_{org_uuid}_help_center`, `org_{org_uuid}_gap_registry`. Data van org A is structureel onbereikbaar voor org B -- niet via filter, maar via collectie-routing in de applicatielaag. Een filterbug kan nooit leiden tot cross-tenant datalekken. Shared collection + payload filter is expliciet afgewezen; zie Appendix A voor de volledige motivering.

```python
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, Distance, SparseVectorParams,
    Filter, FieldCondition, MatchAny,
    models,
)

def create_tenant_collection(client: QdrantClient, org_id: str):
    """Aanmaken bij org-onboarding. Idempotent (exist_ok=True)."""
    collection_name = f"org_{org_id}_help_center"
    client.create_collection(
        collection_name=collection_name,
        vectors_config={
            "dense": VectorParams(size=1024, distance=Distance.COSINE),
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(),
        },
    )
    for field in ["lang", "article_id", "category", "visibility"]:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

# Retrieval: applicatielaag routeert naar correcte collectie op basis van org_id
results = client.query_points(
    collection_name=f"org_{current_org_id}_help_center",
    prefetch=[
        models.Prefetch(query=dense_vector, using="dense", limit=100),
        models.Prefetch(query=sparse_vector, using="sparse", limit=100),
    ],
    query=models.FusionQuery(fusion=models.Fusion.RRF),
    query_filter=Filter(must=[
        FieldCondition(key="visibility", match=MatchAny(any=allowed_visibility)),
    ]),
    limit=20,
)
```

`allowed_visibility` is afhankelijk van de interface: interne chat krijgt `["internal", "external", "public"]`, externe webchat krijgt `["external", "public"]`, publieke KB krijgt `["public"]`.

**Metadata-schema per chunk:**

```json
{
  "chunk_id": "uuid",
  "source_type": "article | extracted_knowledge",
  "article_id": "notion-page-id",
  "article_url": "https://help.example.com/nl/artikel-slug",
  "article_title": "Titel van het helpartikel",
  "section_header": "## Sectie-naam",
  "language": "nl | en | de | ...",
  "product": "ProductNaam",
  "content_type": "procedure | faq | troubleshooting | concept",
  "error_code": "ERR-4021",
  "source_call_id": "call-uuid",
  "linked_article_id": "notion-page-id",
  "last_updated": "2025-03-01",
  "org_id": "org-uuid",
  "visibility": "internal"
}
```

---

### 6.2 Embedding model

| Model | Type | Meertalig | Dims | Hybrid search | Kosten |
|---|---|---|---|---|---|
| LaBSE | Sentence similarity | Ja | 768 | Nee | Gratis |
| multilingual-e5-large | Retrieval | Ja | 1024 | Nee | Gratis |
| **BGE-M3** | **Multi-functional** | **170+ talen** | **1024** | **Dense + sparse in 1 model** | **Gratis** |
| voyage-3-large | Retrieval | Ja | variabel | Nee | Betaald |
| voyage-multilingual-2 | Retrieval | Ja | variabel | Nee | Betaald |

**Aanbeveling: BGE-M3** voor self-hosting

BGE-M3 levert dense en sparse retrieval uit één model, zodat je native hybrid search doet zonder aparte BM25-indexering. Op MIRACL (18 talen): nDCG@10 = 70,0 vs. multilingual-E5 ~65,4. Combineert direct met `bge-reranker-v2-m3` tot een volledig open source pipeline.

Kritische noot op LaBSE: getraind voor sentence similarity, niet retrieval. Rankt near-last op retrieval-benchmarks. Niet gebruiken voor dit use case.

Als quality-first en betaalde API acceptabel: **voyage-3-large** (#1 op 100 datasets verspreid over 8 domeinen).

**Query/document asymmetrie** -- gebruik de juiste instructie-prefixes met BGE-M3:
- Query: `"Represent this sentence for searching relevant passages: "`
- Document: lege prefix

---

### 6.3 Chunking en structuur

**Markdownartikelen met wikilinks: hybride aanpak in twee fasen**

Fase 1 -- `MarkdownHeaderTextSplitter`: splits op `##` en `###` headers. Elke chunk krijgt header-hiërarchie als metadata (`section_header`, `parent_header`).

Fase 2 -- `RecursiveCharacterTextSplitter` op te grote chunks: target 400-512 tokens, overlap 50-80 tokens. Splits nooit midden in een codeblok.

Wikilinks (`[[Artikelnaam]]`): verwijder ze niet, zet ze om naar metadata:

```python
from langchain.text_splitter import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
import re

def parse_wikilinks(text: str) -> tuple[str, list[str]]:
    links = re.findall(r'\[\[([^\]]+)\]\]', text)
    clean_text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    return clean_text, links

headers_to_split_on = [("#", "h1"), ("##", "h2"), ("###", "h3")]
md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on)
char_splitter = RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=64)

chunks = md_splitter.split_text(markdown_content)
final_chunks = []
for chunk in chunks:
    if len(chunk.page_content) > 600:
        sub = char_splitter.split_documents([chunk])
        final_chunks.extend(sub)
    else:
        final_chunks.append(chunk)
```

**Geëxtraheerde JSON-kennisitems: synthetische tekst bouwen**

Embeddings van sleutel-waarde-pairs zijn minder goed dan embeddings van natuurlijke taalzinnen. Bouw altijd een leesbare tekstvorm:

```python
def json_item_to_text(item: dict) -> str:
    return f"""
Probleem: {item['problem_summary']}
Oplossing: {item['resolution']['resolution_description']}
Stappen: {'; '.join(item['steps_taken'])}
Product: {item['product_area']}
""".strip()
```

**Alternatief voor structuur-aware chunking: LlamaIndex HierarchicalNodeParser**

`MarkdownHeaderSplitter` + `RecursiveCharacterTextSplitter` produceert chunks met header-metadata. `LlamaIndex HierarchicalNodeParser` doet hetzelfde maar bouwt bovendien expliciete `PARENT`/`CHILD`-relaties tussen chunks en de bronpagina. De `AutoMergingRetriever` kan dan automatisch kleinere chunks samenvoegen tot hun parent wanneer een meerderheid van de siblings retrieved wordt. Zinvol als je wilt dat de LLM volledige sectie-context ziet in plaats van losse fragments.

Beide aanpakken werken met Qdrant; kies LlamaIndex als je de parent-child traversal bij retrieval nodig hebt.

**Teruglinken van JSON-item naar artikel:**

1. Bij extractie (voorkeur): laat het LLM bij extractie het meest relevante artikel-ID meegeven als `linked_article_id`.
2. Post-hoc: embed elk JSON-item, doe nearest-neighbor-search in de artikelen-index. Drempel cosine similarity > 0,75.

---

### 6.4 Hybrid search en reranking

Hybrid search is altijd zinvol voor helpdesk-gebruik: foutcodes, productnamen en exacte procedure-stappen zijn keyword-matches die puur semantische search mist.

**Architectuur: drie lagen**

```
Query
  ├─ BM25/sparse retrieval (top-50)    ← exacte trefwoorden, foutcodes
  ├─ Dense retrieval (top-50)          ← semantische betekenis
  └─ RRF-fusie (k=60)
       └─ bge-reranker-v2-m3 (top-5 of top-10)
            └─ LLM met retrieved context + metadata
```

**Fusie:** Reciprocal Rank Fusion (RRF, k=60). Geen tuning nodig, robuust als startpunt. Pas later over naar gewogen combinatie als je gelabelde queries hebt.

**Reranker:** `BAAI/bge-reranker-v2-m3` -- open source, multilingual, sluit direct aan op BGE-M3. Rerank alleen de top-20-50 candidates, niet de volledige index.

**Meertaligheid:** BGE-M3's gedeelde embedding-space maakt cross-lingual retrieval mogelijk -- een Nederlandse query vindt Engelse documenten en vice versa. Je hoeft geen aparte indexes per taal bij te houden. Gebruik de metadata-filter `language` na retrieval om interface-taal af te dwingen indien gewenst.

---

### 6.5 Artikellinks door de pipeline

**Kernprincipe: bewaar metadata door de hele pipeline**

```
Chunk ingested
  └─ metadata.article_url = "https://help.example.com/nl/slug"
       └─ Opgeslagen in Qdrant naast de vector

Query
  └─ Retrieval geeft chunks terug met volledige metadata
       └─ Top-K chunks doorgegeven aan LLM
            └─ Prompt instrueert: citeer ALTIJD de bron met URL
                 └─ Response bevat antwoord + URLs
```

**Prompt-patroon voor gegarandeerde bronvermelding:**

```
Beantwoord de vraag op basis van de onderstaande bronnen.
Vermeld ALTIJD aan het einde van je antwoord de relevante bronnen.

Bronnen:
[1] Titel: {chunk.metadata.article_title}
    URL: {chunk.metadata.article_url}
    Inhoud: {chunk.page_content}

[2] ...

Vraag: {user_question}

Geef je antwoord, gevolgd door:
Bronnen: [1], [2], ...
```

**Anti-hallucination -- grounded response:**

```
Als het antwoord niet in de bronnen staat, zeg dan:
"Ik heb geen informatie over dit specifieke probleem.
Wil je doorverbonden worden met een medewerker?"
```

**Response-formaat per interface:**

```python
# Medewerker
{"answer": str, "sources": [{"title": str, "url": str, "excerpt": str}]}

# Klant
{"answer": str, "sources": [{"title": str, "url": str}], "handoff_option": bool}
```

`handoff_option` activeer je op basis van: lage retrieval-score (confidence < drempel), patroon "ik wil een mens spreken", of specifieke metadata-tags (bijv. `content_type: "billing"`).

---

### 6.6 Volledige architectuuroverview

```
Ingestion pipeline (kennisbasis -- eenmalig + incrementeel):
  sitemap.xml
    └─ parse_sitemap (hreflang --> url_map per pagina)
         └─ Crawl4AI async fetch (validate_sitemap_lastmod)
              └─ content_hash check --> sla ongewijzigde pagina's over
                   └─ MarkdownHeaderSplitter + RecursiveCharacterSplitter
                        └─ Contextual Retrieval (Claude Haiku + prompt caching, ~$5 eenmalig)
                             └─ HyPE: 4 vragen per chunk (Claude Haiku)
                                  └─ BGE-M3 embedding (vragen, niet chunk-tekst)
                                       └─ Qdrant (nav_path + article_id + lang + url_map)

JSON-kennisitems (uit transcripten):
  Transcript --> LLM extractie (Instructor + Haiku)
    └─ Tekst-reconstructie (probleem + oplossing + stappen)
         └─ BGE-M3 embedding
              └─ linked_article_id via nearest-neighbor of extractie

Retrieval:
  Query
    ├─ BGE-M3 sparse (BM25-achtig)
    ├─ BGE-M3 dense
    └─ RRF(k=60) --> bge-reranker-v2-m3 --> top-5 chunks
         └─ Payload-filter op lang (optioneel: alleen Nederlandse artikelen)

Generatie:
  Claude via LiteLLM
    └─ Prompt met chunks + nav_path + url_map + bron-instructie
         └─ Response met answer + article URLs (per taal beschikbaar via url_map)
```

**Framework voor retrieval-orkestratie:** Haystack (deepset) is de aanbevolen keuze: productierijp (The Economist, Oxford University Press), directe Qdrant-integratie, en [Hayhooks](https://github.com/deepset-ai/hayhooks) exposeert pipelines direct als REST API of MCP Server zonder extra framework. LlamaIndex is een volwaardig alternatief voor complexere retrieval-orkestratie. LangChain vereist meer handmatige assemblage en breekt vaker bij LLM-API-updates.

---

## 7. Gap-detectie

### 7.1 Semantische vergelijking in de praktijk

Het basisprincipe: embed het kennisitem, doe een ANN-zoekopdracht in je vector DB, beoordeel de top-k resultaten. De complicatie zit in drempelwaarden en de "gedeeltelijke match"-classificatie.

**Cosine similarity drempelwaarden (empirisch, geen harde wetenschap):**

| Score | Interpretatie |
|---|---|
| > 0,90 | Vrijwel identiek -- waarschijnlijk al gedekt |
| 0,75 -- 0,90 | Onderwerp gedekt, maar dit geval mogelijk niet -- onderzoek verder |
| 0,60 -- 0,75 | Zwakke overlap -- partial match of aanverwant |
| < 0,60 | Nieuw onderwerp |

**Kritieke kanttekening:** ICLR 2025 en recente RAG-surveys bevestigen dat cosine similarity arbitraire resultaten kan geven bij specifieke domeintermen, foutcodes en productnamen. Puur op cosine vertrouwen is onvoldoende.

**Volledig nieuw vs. partial match onderscheiden**

Het probleem: als artikel A gaat over "printer verbindingsfout bij Windows", en het kennisitem is "printer verbindingsfout bij Mac", dan is de cosine similarity hoog (0,82) maar het geval niet gedekt.

Aanpak: twee-fase oordeel:

```
Fase 1: Bi-encoder retrieval --> top-5 kandidaten (snel, schaalbaar)
Fase 2: Cross-encoder of LLM-judge --> beoordeel elk paar (traag, nauwkeurig)
```

De cross-encoder leest query + document samen en beoordeelt relevantie op claim-niveau, niet op onderwerp-niveau. Dat is het verschil tussen "Windows vs. Mac" detecteren.

Alternatief (goedkoper): **RAGChecker** van Amazon -- splitst het kennisitem op in atomaire claims en checkt welke claims terugkomen in het artikel (`claim_recall`). Claims met lage recall = de gap.

---

### 7.2 Meertalige gap-detectie

**Aanbeveling: crosslingual embeddings, geen vertaling eerst.**

Redenen: vertaling voegt latency en kosten toe, introduceert semantische drift bij technische termen, en state-of-the-art crosslingual modellen presteren goed voor NL/EN. BGE-M3's kwaliteit is onderbouwd door de MIRACL benchmark (nDCG@10 = 70,0 over 18 talen); resultaten op crosslingual competities zijn gemengd en taakafhankelijk.

| Model | Talen | Kwaliteit | Hybrid search |
|---|---|---|---|
| paraphrase-multilingual-mpnet-base-v2 | 50+ | Hoog | Nee |
| paraphrase-multilingual-MiniLM-L12-v2 | 50+ | Goed | Nee |
| **BGE-M3** | **100+** | **State-of-art** | **Ja** |
| LaBSE | 109 | Hoog voor bitext | Nee |

BGE-M3 is de sterkste keuze: ondersteunt 100+ talen (inclusief toekomstige DE/FR/ES/PT), doet dense + sparse retrieval tegelijk, en is geëvalueerd op crosslingual benchmarks.

---

### 7.3 Architecturen: wanneer wat

| Schaal kennisbasis | Aanpak |
|---|---|
| < 500 artikelen | Pure cosine similarity is prima |
| 500 -- 10.000 | Bi-encoder (FAISS/Qdrant ANN) |
| > 10.000 of hoge nauwkeurigheid vereist | Bi-encoder + cross-encoder reranking |

De productie-architectuur is altijd een funnel:

```
Bi-encoder retrieval (top-20) --> Cross-encoder reranking (top-5) --> Classificatie
```

Voor batch-verwerking (tientallen/honderden gesprekken per run) is de cross-encoder in de reranking-stap prima betaalbaar -- er is geen real-time latency-eis.

**GraphRAG: overkill voor gap-detectie**

GraphRAG voegt waarde toe wanneer je multi-hop redenering nodig hebt ("artikel A verwijst naar concept B dat uitgelegd wordt in artikel C, maar de gap zit in de relatie tussen A en C"). Voor helpdesk-gaps -- "dit probleem staat niet in het artikel" -- is die relatie-informatie niet nodig. Bewaar GraphRAG voor een latere fase wanneer je wikilinks in je Notion-artikelen wil exploiteren.

---

### 7.4 LLM-as-a-judge

Zinvol voor gap-beoordeling, maar op de juiste plek in de pipeline.

Vectara's FaithJudge (2025) bereikt 84% balanced accuracy voor RAG-evaluatie. RAGChecker gebruikt claim-level entailment, direct vertaalbaar naar gap-detectie.

**Gebruik LLM-as-a-judge voor:**
- Het definitieve oordeel over "gedekt / partial / nieuw" na de retrieval-fase
- Het genereren van de uitleg voor de redacteur ("dit artikel mist de stappen voor Mac-gebruikers")
- Het groeperen van geaggregeerde gaps in een bruikbare schrijfopdracht

**Gebruik LLM-as-a-judge NIET voor:**
- De eerste retrieval-stap (te traag, te duur)
- Deduplicatie van gaps (embedding clustering is goedkoper)

**Prompt-structuur:**

```
Artikel: [volledige tekst of samenvatting]
Kennisitem: {probleem, oplossing, stappen, foutcode}

Vraag: Dekt dit artikel dit kennisitem volledig, gedeeltelijk, of niet?
- VOLLEDIG: alle stappen en context aanwezig
- GEDEELTELIJK: onderwerp aanwezig, maar [specifiek aspect] ontbreekt
- NIEUW: dit onderwerp staat niet in het artikel

Geef een JSON-antwoord: {"oordeel": "...", "ontbrekende_aspecten": [...]}
```

---

### 7.5 Aggregatie en prioritering

**Twee-staps clustering**

Stap 1 -- gap-deduplicatie per run: bereken cosine similarity tussen alle geëxtraheerde gaps. Groepeer gaps met similarity > 0,85 als "hetzelfde probleem". Neem de mediaan-vector als canonical gap.

Stap 2 -- aggregatie over runs: sla canonical gaps op in een aparte Qdrant-collectie (de "gap registry"). Bij elke nieuwe run: match nieuwe gaps tegen de registry.

```python
# Pseudocode gap-aggregatie
gap_registry = qdrant.collection(f"org_{org_id}_gap_registry")

for gap in new_gaps:
    similar = gap_registry.search(gap.embedding, limit=1)
    if similar.score > 0.85:
        gap_registry.update(similar.id, {
            "frequency": similar.frequency + 1,
            "last_seen": today,
            "conversations": similar.conversations + [gap.conversation_id]
        })
    else:
        gap_registry.insert({
            "embedding": gap.embedding,
            "canonical_text": gap.text,
            "frequency": 1,
            "first_seen": today
        })
```

**Clustering van vergelijkbare gaps:** BERTopic + HDBSCAN is de standaard aanpak. HDBSCAN gooit outliers weg ("noise") -- voor helpdesk-gaps is dit juist gewenst: zeldzame eenmalige problemen hoeven geen hoog-prioriteit artikel te krijgen. Stem `min_cluster_size` af op je volume (50 gesprekken: min 2-3; 500 gesprekken: min 10+).

**Prioriteringsformule:**

```
prioriteit_score = frequentie × urgentie_gewicht × recency_factor

waarbij:
  frequentie       = aantal gesprekken met deze gap
  urgentie_gewicht = 2.0 als foutcode aanwezig, 1.5 als escalatie, 1.0 normaal
  recency_factor   = exponentieel afnemend over tijd (recent = hoger gewicht)
```

**Output voor de redacteur** (Intercom Fin-model):

```
GAP-RAPPORT #47
─────────────────────────────────────────
Type:        Aanvulling op bestaand artikel
Artikel:     "Printer verbinding instellen" (similarity: 0.82)
Frequentie:  23 gesprekken (laatste 30 dagen)
Prioriteit:  HOOG

Wat ontbreekt:
- Stappen voor macOS 14+ (Sonoma)
- Foutcode: "Error 0x000007E5" niet gedekt
- Specifiek voor product: LaserJet Pro M404n

Voorbeeld-antwoord van agent (gesprek #A-2847):
"Voor Mac gebruikers: ga naar Systeemvoorkeursinstellingen..."

Gerelateerde gesprekken: #A-2847, #A-2901, #A-3012, [+20]
```

Menselijke review blijft in de loop. Het systeem genereert een geprioriteerde actie-inbox, geen auto-publicatie.

---

### 7.6 Minimale werkende architectuur

```
BATCH INPUT
  JSON kennisitems uit transcripten
  {probleem, oplossing, product, stappen, foutcode, taal}
      |
      v
EMBEDDING LAAG
  BGE-M3 (sentence-transformers)
  - Embed zowel kennisitems als artikelchunks
      |
      v
RETRIEVAL (Qdrant)
  Collectie 1: artikel_embeddings (Notion markdown chunks)
  Collectie 2: gap_registry (geaggregeerde gaps)
  Per kennisitem: top-5 artikelen ophalen (hybrid ANN search)
      |
      v
CLASSIFICATIE (2-fase)
  Fase 1 (snel): cosine similarity drempel
    > 0.90 --> SKIP (al gedekt)
    0.60 – 0.90 --> door naar fase 2
    < 0.60 --> NIEUW (direct naar gap registry)

  Fase 2 (nauwkeurig): LLM-as-a-judge (Claude Haiku)
    Input: kennisitem + top-3 artikel-chunks
    Output: {oordeel, ontbrekende_aspecten, artikel_id}
      |
      v
AGGREGATIE
  Match nieuwe gaps met gap_registry (cosine > 0.85)
  - Bestaand: increment frequentie
  - Nieuw: voeg toe
      |
      v
OUTPUT: GEPRIORITEERDE GAP-LIJST
  Gesorteerd op: frequentie × urgentie × recency
  Per gap: type, artikel, frequentie, voorbeeld-antwoord
```

**Tool-selectie:**

| Component | Tool | Reden |
|---|---|---|
| Vector DB | Qdrant | Open source, snel, metadata-filtering |
| Embedding | BGE-M3 (sentence-transformers) | Best crosslingual, 100+ talen |
| Reranker | bge-reranker-v2-m3 | Multilingual (100+ talen), sluit direct aan op BGE-M3 |
| LLM-judge | Claude Haiku | Goedkoop voor batch, structured output |
| Gap clustering | BERTopic of agglomeratieve clustering | Zie 7.5 |
| Orchestratie | Python script of simpele Prefect flow | Batch, geen real-time nodig |
| Evaluatie | RAGChecker | Claim-recall metriek |
| Taxonomie review | Argilla | Human-in-the-loop classificatie review; zie sectie 8.2 |

---

### 7.7 Bekende valkuilen

**1. Chunking van artikelen is kritiek**
Embed artikelen niet als geheel (markdown van 3.000 woorden). Chunk per sectie (H2/H3) -- zie sectie 6.3. Embeddings van volledige artikelen zijn te generiek om partial matches te detecteren.

**2. Foutcodes en productnamen worden slecht gevonden door dense retrieval**
Gebruik hybrid search (Qdrant BM25 + dense). Exacte foutcode-match via BM25, semantische context via dense.

**3. LLM-judge drift**
Bij model-upgrade of prompt-aanpassing veranderen oordelen. Houd een testset van 50 gelabelde gaps bij om regressies te detecteren.

**4. Afhankelijkheid van extractie-kwaliteit**
Gap-detectie is alleen zo goed als de extractie. Als het kennisitem `{stappen: null, foutcode: null}` bevat, vindt de analyse niets nuttigs. Bouw kwaliteitschecks in vóór de embedding-stap.

**5. Artikelversies**
Notion-markdown die gewijzigd wordt moet opnieuw geïndexeerd worden. Sla een `article_hash` op naast de embedding en hercompute alleen bij wijziging.

---

### 7.8 Afhankelijkheden tussen lagen

- **Extractie --> gap-detectie:** voeg `confidence_score` toe vanuit de extractor. Items met lage confidence worden minder zwaar gewogen in de prioritering.
- **Storage --> gap-detectie:** zorg dat Notion-artikelen worden gechunked bij ingest, niet bij query-tijd. Sla de sectie-titel op als metadata -- gebruik je in de output voor de redacteur ("sectie 'Installatie op Mac' ontbreekt").
- **Gap-detectie --> artikel-update:** koppel de gap-ID aan de Notion-pagina-ID, zodat een later systeem automatisch een draft-PR kan aanmaken op de juiste locatie.

---

### 7.9 Feedback-loop: van gedetecteerde gap naar opgelost artikel

Het systeem detecteert en aggregeert gaps, maar zonder sluitingsmechanisme groeit de redactie-inbox oneindig. De feedback-loop verbindt de gap-registry met het publicatieproces.

**Lifecycle van een gap:**

```
open → in_progress → resolved
```

**Gap-registry schema-aanvulling:**

```python
# Voeg toe aan het gap-record bij insert
{
    "embedding": [...],
    "canonical_text": "...",
    "frequency": 1,
    "first_seen": "2025-03-01",
    "last_seen": "2025-03-01",
    "status": "open",           # "open" | "in_progress" | "resolved"
    "resolving_article_id": None,  # Notion page ID van het nieuwe/bijgewerkte artikel
    "resolved_at": None,
}
```

**Gap sluiten na publicatie:**

```python
def resolve_gap(gap_id: str, article_id: str):
    gap_registry.set_payload(
        points=[gap_id],
        payload={
            "status": "resolved",
            "resolving_article_id": article_id,
            "resolved_at": datetime.today().isoformat(),
        }
    )

# Filter in de prioriterings-query: alleen open gaps tonen
from qdrant_client.models import Filter, FieldCondition, MatchValue

open_gaps = gap_registry.scroll(
    scroll_filter=Filter(
        must=[FieldCondition(key="status", match=MatchValue(value="open"))]
    )
)
```

**Automatische hervalidatie na nieuwe artikelingest:**

Na elke Notion-ingest: voer een similarity-check uit tussen het nieuwe artikel-embedding en de open gaps in de registry. Gaps met cosine similarity > 0,90 worden automatisch gemarkeerd als `resolved`. Gaps tussen 0,75 en 0,90 worden gemarkeerd als `in_progress` voor handmatige check.

**Risico: vals-positief resolved**

Een artikel kan een gap oppervlakkig lijken te dekken (hoge similarity op trefwoorden) zonder de kern te raken. Aanbeveling: gaps die automatisch als `resolved` worden gemarkeerd, worden in de eerstvolgende extractieronde opnieuw geëvalueerd. Als hetzelfde probleem opnieuw voorkomt in ≥ 3 gesprekken, wordt de gap heropend.

---

### Feedback-loop in het Knowledge-platform

De gap-registry is niet beperkt tot helpdesk-transcripten. Elke exposure-interface genereert gesprekken die via dezelfde adapter worden verwerkt.

```
Externe webchat-gesprek
  --> Helpdesk-adapter (zelfde pipeline)
        --> org_id meegeven van de klantorganisatie
  --> Gap-detectie --> redactie-inbox (gefilterd op org_id)
  --> Artikel bijgewerkt --> Knowledge opnieuw geindexeerd
  --> Betere antwoorden --> minder escalaties
```

Dit maakt de kennisbank zelfverbeterend: hoe meer gebruik van de webchat, hoe completer de kennisbank wordt. De redactie-inbox in het Knowledge-platform toont gaps geaggregeerd per organisatie, zodat elke klant zijn eigen inbox beheert.

Elke organisatie heeft een eigen `org_{org_id}_gap_registry` Qdrant-collectie. Gaps van organisatie A zijn structureel onbereikbaar voor organisatie B -- niet via filter, maar via collectie-routing, consistent met het collection-per-tenant patroon in sectie 6.1.

---

## 8. Entity-laag, taxonomie en routing

De RAG-laag (Qdrant) beantwoordt inhoudsvragen: wat staat er in een artikel, wat zei een klant over X. Maar een tweede klasse vragen is structureel anders: hoeveel gesprekken noemen integratie Y, welke foutcodes hebben de meeste open gaps, wat is de resolutierate per product area. Dit zijn analytische vragen. Vectorsearch is het verkeerde instrument daarvoor. Deze sectie beschrijft een aanvullende structuurlaag die naast Qdrant werkt -- niet ter vervanging.

---

### 8.1 Drie toevoegingen aan de pipeline

De volgende drie elementen zijn niet noodzakelijk voor een werkende eerste versie, maar worden de bottleneck zodra het systeem op schaal draait en redacteuren betrouwbare prioritering verwachten.

**1. Taxonomy via automatische clusterontdekking**

Er is geen vooraf gedefinieerde taxonomy. De taxonomy ontstaat uit de data van de tenant zelf -- volledig automatisch, zonder dat iemand categorieën hoeft te bedenken of te benoemen.

De pipeline bij eerste upload:

```
Documenten/transcripten geüpload
  --> chunken + embedden (BGE-M3)
  --> BERTopic + HDBSCAN: ontdek clusters in de data
  --> LLM benoemt elk cluster automatisch op basis van
      de meest representatieve fragmenten
  --> Clusters worden taxonomy-nodes in SQLite
```

HDBSCAN gooit ruis weg -- clusters die nergens bij horen worden niet als categorie opgenomen. Wat overblijft zijn patronen die daadwerkelijk in de data zitten. De LLM genereert een naam en korte beschrijving per cluster op basis van de inhoud, niet op basis van een template.

Het resultaat is een taxonomy in de taal en het vocabulaire van de tenant, zonder dat iemand er iets voor hoeft te doen.

**2. Automatische entiteitsextractie en -opbouw**

Er is geen handmatig beheerde entiteitslijst. Entiteiten worden geëxtraheerd uit de data zonder vooraf gedefinieerd schema -- vergelijkbaar met hoe Graphiti/Zep dit doet.

Bij elke ingest extraheert het systeem entiteiten als vrije strings. Daarna volgt entiteitsresolutie in drie stappen:

1. **Exacte match** tegen reeds bekende entiteiten in SQLite
2. **Fuzzy match** (Levenshtein-drempel) voor spellingsafwijkingen
3. **LLM-judge** voor semantisch vergelijkbare entiteiten: is dit dezelfde entiteit of een nieuwe?

Nieuwe entiteiten die nergens op matchen worden automatisch als nieuwe rij aan de SQLite-entiteitentabel toegevoegd. De tabel groeit mee met de data -- niemand beheert hem handmatig.

**3. SQLite entity registry**

Een lichtgewicht SQLite-database naast Qdrant. Geen vervanging -- aanvulling. De structuur:

```sql
CREATE TABLE entities (
    id TEXT PRIMARY KEY,        -- canonieke ID (ent_sf_001)
    name TEXT NOT NULL,         -- weergavenaam (Salesforce)
    type TEXT NOT NULL,         -- integration | product | error_code | topic
    taxonomy_path TEXT          -- billing/invoices
);

CREATE TABLE conversation_entities (
    conversation_id TEXT,
    entity_id TEXT,
    resolved INTEGER,           -- 0 of 1
    gap_signal TEXT,            -- none | weak | strong
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);

CREATE TABLE gap_entities (
    gap_id TEXT,
    entity_id TEXT,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
```

Dit maakt de analytische vragen exact en snel:

```sql
-- Welke integraties hebben de meeste onopgeloste gesprekken?
SELECT e.name, COUNT(*) as total, SUM(1 - ce.resolved) as unresolved
FROM conversation_entities ce
JOIN entities e ON ce.entity_id = e.id
WHERE e.type = 'integration'
GROUP BY e.name
ORDER BY unresolved DESC;
```

De entiteits-ID is de verbindingssleutel tussen SQLite en Qdrant. Elk Qdrant-vector-payload bevat `entity_ids: ["ent_sf_001"]`. Zo kan een antwoord context uit beide lagen combineren: semantische inhoud uit Qdrant, frequentie- en resolutiedata uit SQLite.


---

### 8.2 Taxonomie-evolutie: twee jobs, twee regimes

Er zijn twee dingen die allebei "taxonomiebeheer" heten, maar fundamenteel anders zijn.

**Job 1: document-classificatie** — wanneer een nieuw document binnenkomt, bepaal naar welke bestaande categorie het gaat. Dit is volledig automatiseerbaar. Een model dat traint op de goedgekeurde taxonomy kan inkomende documenten nauwkeurig en op schaal taggen.

**Job 2: taxonomie-curatie** — beslissen of een nieuwe categorie moet bestaan, of twee categorieën samengevoegd moeten worden, of een naam nog klopt, of een splitsing nodig is. Dit is niet automatiseerbaar op acceptabele nauwkeurigheid. Enterprise Knowledge's analyse uit maart 2025 is direct: "to date there has been no ML or AI application or framework that can replace human decision making in this sphere." De reden: deze beslissingen vereisen organisatorische context die het systeem niet heeft.

De architectuur in sectie 8.1 (BERTopic voor initiële clusterontdekking) is correct als **voorstelgenerator**. De fout zit in het automatisch activeren van voorstellen zonder reviewstap.

**Bekende productieproblemen met volledig automatische taxonomy-evolutie:**

- BERTopic's decay-mechanisme bij incrementeel leren (nieuwere documenten wegen zwaarder) maakt dat de taxonomy na zes maanden anders is dan bij activering, ook als de werkelijke onderwerpen niet veranderd zijn. Category IDs driften. Downstream tagging breekt.
- Twee runs op dezelfde documenten produceren niet gegarandeerd dezelfde clusterstructuur. Het proces bevat standaard willekeur. Twee mensen die de taxonomy onafhankelijk opzetten krijgen verschillende resultaten.
- Tussen 20 en 40 procent van documenten in een echte collectie eindigt als outlier: ze zitten tussen clusters in, of dekken onderwerpen die het model niet als significant detecteerde. Dit zijn geen ruis-documenten. Ze zijn legitieme kennis die het model niet kon plaatsen.

**De juiste loop:**

```
Nieuwe data binnengekomen
  --> embed chunks (BGE-M3)
  --> vergelijk met bestaande taxonomy-nodes (cosine similarity)
  --> similarity > 0.75: auto-classificeer naar dichtstbijzijnde node (geen review nodig)
  --> similarity < 0.75: voeg toe aan unclassified pool

Unclassified pool > drempelwaarde (bijv. 50 items)
  --> BERTopic clustert de pool
  --> LLM benoemt nieuwe clusters + detecteert merge/split-kandidaten
  --> Voorstellen in review-queue (SQLite: status = 'pending')
  --> Reviewer keurt goed / verwerpt / hernoemt
  --> Na goedkeuring: SQLite bijgewerkt, Qdrant payloads bijgewerkt
```

Document-classificatie naar bestaande nodes draait volledig automatisch. Taxonomy-structuurwijzigingen (nieuwe node activeren, samenvoegen, hernoemen, splitsen) vereisen altijd een menselijke beslissing.

**Overhead van de reviewstap:**

Onderzoek naar active learning annotation legt de tijdsinvestering op twee tot vier uur per kwartaal voor een enkele reviewer die een collectie van enkele duizenden documenten beheert, als de queue alleen de onzekere gevallen toont. Dit is de overhead van het correct uitvoeren van het proces. Het alternatief is een taxonomy die drifts, onbetrouwbare classificaties produceert, en uiteindelijk een grotere schoonmaak vereist.

**Qdrant payload-updates zijn goedkoop:**

Een taxonomy-aanpassing vereist geen herberekening van embeddings. Alleen de metadata-velden in de Qdrant-payload worden bijgewerkt na goedkeuring -- een snelle schrijfoperatie over de betreffende vectors.

**Review-interface:**

De review-queue is een standaard onderdeel van de pipeline, niet optioneel. Zie het UI-ontwerp voor de taxonomie-tab in de kennisbank-interface (onder `/app/knowledge/`).

---

### 8.3 Routing: wanneer gaat een vraag naar RAG, wanneer naar SQLite?

Er zijn twee complementaire mechanismen.

**Mechanisme 1: hardcoded router (voor bekende vraagpatronen)**

Detecteer de intentie op basis van taalpatronen voordat het LLM betrokken raakt:

```python
ANALYTIC_SIGNALS = ["hoeveel", "meest", "vaakst", "rate", "percentage",
                    "top", "ranking", "trend", "welke integratie", "hoe vaak"]

CONTENT_SIGNALS = ["wat staat er", "hoe werkt", "leg uit", "wat zei",
                   "welke stappen", "is er een artikel over"]

def route(query: str) -> str:
    q = query.lower()
    if any(s in q for s in ANALYTIC_SIGNALS):
        return "sql"
    if any(s in q for s in CONTENT_SIGNALS):
        return "rag"
    return "both"
```

Dit is sub-milliseconde snel en ~82% accuraat. Geschikt voor dashboards en vaste rapportage-queries.

**Mechanisme 2: tool use (voor een chat-interface)**

Geef het LLM twee gereedschappen met een duidelijke beschrijving. Het model kiest zelf op basis van de vraag:

```python
tools = [
    {
        "name": "search_knowledge_base",
        "description": (
            "Zoek in artikelen en gespreksinhoud via semantische similarity. "
            "Gebruik dit voor: wat staat er in een artikel, hoe werkt X, "
            "welke stappen zijn beschreven, wat zei een klant over Y."
        ),
        "input_schema": {"query": {"type": "string"}}
    },
    {
        "name": "query_entity_database",
        "description": (
            "Bevraag gestructureerde data over entiteiten, frequenties en relaties. "
            "Gebruik dit voor: hoeveel gesprekken, welke integratie het vaakst, "
            "resolutierate per product area, open gaps per entiteit."
        ),
        "input_schema": {"sql_query": {"type": "string"}}
    }
]
```

Voor analytische vragen genereert het LLM SQL. Voor inhoudsvragen triggert het de RAG-pipeline. Beide resultaten kunnen gecombineerd in één antwoord verschijnen.

**Hoe de twee lagen verbonden zijn:**

De entiteits-ID is de verbindingssleutel. Qdrant-payloads bevatten `entity_ids`. SQLite gebruikt diezelfde IDs als primary key. Een gecombineerd antwoord ziet er zo uit:

```
[Uit Qdrant]  Gap: klant kon geen webhook instellen via Salesforce-integratie.
              Voorbeeld-antwoord agent: "Dat vereist een Enterprise-licentie..."
[Uit SQLite]  Salesforce (ent_sf_001): 47 gesprekken, 12 onopgelost, 3 open gaps.
```

Beide blokken gaan als context de LLM-prompt in. Het model combineert ze tot een antwoord met zowel inhoud als context.

---

## 9. Handoff naar menselijke agent

`handoff_option: true` (sectie 6.5) triggert de handoff-flow. Op dat moment heeft de widget een actief AI-gesprek met een transcript. Er zijn twee aanpakken om dat gesprek over te dragen aan een menselijke agent.

---

### Optie A: Widget Switch

**Principe:** Na handoff verdwijnt de eigen widget. De eindgebruiker stapt over naar de native chat-interface van het helpdeskplatform.

**Datavloed:**

```
AI besluit tot handoff
  --> POST /api/handoff (transcript, samenvatting, user identity)
        --> Backend maakt conversation aan via platform API
        --> Backend post transcript als berichten in die conversation
        --> Backend retourneert: { widgetUrl, conversationId, identityToken }
  --> Widget toont "Je wordt verbonden..."
  --> Platform widget wordt geinitialiseerd met identityToken
  --> Eindgebruiker vervolgt gesprek in platform widget
```

**Wat je bouwt:**
- Backend endpoint `POST /api/handoff`: maakt conversation aan, post transcript, retourneert widget-initialisatiedata
- Minimale frontend logica: eigen widget verbergen, platform widget initialiseren met de juiste identity-token

**Voordelen:**
- Minimale infrastructuur: geen webhook listener, geen SSE, geen session store
- Agent-replies lopen via het platform zelf: geen doorstuurlogica nodig
- Eenvoudig te debuggen en te onderhouden

**Nadelen:**
- Zichtbare UX-breuk: de eindgebruiker wisselt van interface
- Identity handshake vereist dat het platform zijn eigen JS-snippet op de pagina geladen is
- Als de eindgebruiker de pagina herlaadt, kan de sessie verloren gaan (afhankelijk van het platform)

---

### Optie B: Headless / Transparant

**Principe:** De eigen widget blijft zichtbaar gedurende de hele sessie. Agent-replies worden via de backend doorgestuurd naar de widget.

**Datavloed:**

```
AI besluit tot handoff
  --> POST /api/handoff (transcript, samenvatting, user identity)
        --> Backend maakt conversation aan via platform API
        --> Backend post transcript als berichten in die conversation
        --> Backend slaat op in session store: { widgetSessionId --> platformConversationId }
        --> Backend retourneert: { status: "waiting_for_agent" }
  --> Widget toont "Je wordt verbonden..." en houdt SSE-verbinding open

Agent pakt conversation op in zijn tool
  --> Agent stuurt reply
  --> Platform vuurt webhook: POST /webhooks/agent-reply
        --> Backend haalt widgetSessionId op via session store (key: platformConversationId)
        --> Backend pusht reply via SSE naar de juiste widget-sessie
  --> Widget toont agent-reply als chatbericht
```

**Wat je bouwt:**

1. **Backend handoff endpoint** -- identiek aan Optie A (conversation aanmaken, transcript posten)
2. **Session store (Redis):**
   ```
   SET handoff:{platformConvId} {widgetSessionId} EX 86400
   ```
3. **Webhook receiver** (`POST /webhooks/agent-reply`):
   - Verifieer HMAC-handtekening (elk serieus platform stuurt een signature header)
   - Haal `widgetSessionId` op uit Redis via `platformConversationId`
   - Schrijf reply naar het SSE-kanaal van die sessie
4. **SSE endpoint** (`GET /sse/{widgetSessionId}`):
   - Widget houdt deze verbinding open na handoff
   - Backend schrijft agent-replies hiernaar door

**Session store:**

```typescript
// Bij handoff aanmaken
await redis.set(
  `handoff:${platformConversationId}`,
  widgetSessionId,
  { EX: 86400 }  // 24 uur TTL
);

// Bij webhook ontvangst
const widgetSessionId = await redis.get(`handoff:${platformConversationId}`);
if (!widgetSessionId) return;  // Sessie verlopen of onbekend
await sseEmitter.emit(widgetSessionId, { role: "agent", content: reply.text });
```

**Voordelen:**
- Naadloze UX: eindgebruiker merkt niet dat er een ander systeem achter zit
- Volledige controle over het chatvenster en de bericht-styling
- Geen afhankelijkheid van het platform's JS-snippet in de browser

**Nadelen:**
- Meer infrastructuur: Redis, SSE endpoint, webhook listener, session mapping
- Webhook-betrouwbaarheid: als het platform een webhook mist, ontvangt de eindgebruiker de reply niet -- overweeg een fallback-poll als zekerheid
- Extra latency: reply gaat via platform webhook naar jouw backend naar widget (doorgaans <500ms, afhankelijk van platform-webhook-snelheid)

---

### Vergelijking

| | Optie A: Widget Switch | Optie B: Headless |
|---|---|---|
| UX | Zichtbare switch | Naadloos |
| Infrastructuur | Minimaal | Redis + SSE + webhook |
| Identity | Platform JS snippet vereist | Alleen API-side |
| Bouw-inspanning | Klein | Medium |
| Foutgevoeligheid | Laag | Webhook-afhankelijk |
| Aanbevolen voor | PoC, intern gebruik | Productie, klantgerichte chat |

---

### Gedeelde infrastructuur (beide opties)

Ongeacht de gekozen optie zijn de volgende componenten identiek.

**Transcript-formaat bij overdracht:**

```typescript
interface HandoffPayload {
  transcript: Array<{
    role: "user" | "assistant";
    content: string;
    timestamp: string;
  }>;
  summary: string;           // 2-3 zinnen, door AI gegenereerd vlak voor handoff
  user: {
    email?: string;
    name?: string;
    externalId: string;      // Interne user ID
  };
  context: {
    handoff_reason: "low_confidence" | "user_requested" | "escalation_rule";
    last_retrieval_score?: number;
  };
}
```

**Samenvatting genereren voor de agent:**

Genereer de samenvatting als onderdeel van de handoff-call, niet als apart AI-verzoek. Voeg een instructie toe aan de laatste AI-turn: als `handoff_option: true`, genereer dan ook een `agent_briefing` van 2-3 zinnen die de agent direct context geeft zonder het volledige transcript te lezen.

**Platform-agnostische abstractielaag:**

Ontwerp de handoff-backend met een vaste interface zodat het onderliggende platform gewisseld kan worden zonder de widget aan te raken:

```typescript
interface HandoffAdapter {
  createConversation(payload: HandoffPayload): Promise<{ conversationId: string }>;
  parseWebhookReply(
    body: unknown,
    headers: Record<string, string>
  ): AgentReply | null;
}
```

---

## 9. Bronnen

- [Instructor library](https://python.useinstructor.com/)
- [NuExtract 2.0 benchmark](https://numind.ai/blog/outclassing-frontier-llms----nuextract-2-0-takes-the-lead-in-information-extraction)
- [Haystack + NuExtract cookbook (Hugging Face)](https://huggingface.co/learn/cookbook/en/information_extraction_haystack_nuextract)
- [BAML vs Instructor comparison](https://www.glukhov.org/llm-performance/benchmarks/baml-vs-instruct-for-structured-output-llm-in-python/)
- [Chroma Research: Context Rot](https://research.trychroma.com/context-rot)
- [Stanford: Lost in the Middle (TACL 2024)](https://arxiv.org/abs/2307.03172)
- [MIT TACL: When Can LLMs Actually Correct Their Own Mistakes?](https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00713/125177/)
- [arXiv: Structured extraction across English/Dutch/Czech (2025)](https://arxiv.org/html/2511.10658)
- [Builtjes et al.: Dutch clinical extraction on DRAGON tasks (JAMIA Open 2025)](https://academic.oup.com/jamiaopen/article/8/5/ooaf109/8270821)
- [CLIN Journal 2025: NER in Dutch/French/German/English](https://clinjournal.org/clinj/article/download/199/212)
- [AWS Post-Call Analytics sample](https://github.com/aws-samples/amazon-transcribe-post-call-analytics)
- [Simon Willison: Structured data extraction with LLM schemas](https://simonwillison.net/2025/Feb/28/llm-schemas/)
- [ACL 2025: S2R self-correction via RL](https://aclanthology.org/2025.acl-long.1104/)

**Kennisbasis inladen**
- [Crawl4AI -- open source web crawler voor AI](https://docs.crawl4ai.com/)
- [Crawl4AI sitemap seeding](https://docs.crawl4ai.com/core/url-seeding/)
- [Anthropic Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)
- [HyPE: Hypothetical Prompt Embeddings (machinelearningplus)](https://machinelearningplus.com/gen-ai/hype-rag-how-hypothetical-prompt-embeddings-solve-question-matching-in-retrieval-systems/)
- [Qdrant multitenancy en payload-filtering](https://qdrant.tech/articles/multitenancy/)
- [LlamaIndex HierarchicalNodeParser](https://developers.llamaindex.ai/python/framework-api-reference/node_parsers/hierarchical/)
- [Hierarchical chunking: structure preservation](https://app.ailog.fr/en/blog/guides/hierarchical-chunking)

**RAG-architectuur**
- [Vector Stores for RAG Comparison](https://glukhov.org/post/2025/12/vector-db-rag-comparison/)
- [pgvector vs Qdrant](https://tigerdata.io/blog/pgvector-vs-qdrant)
- [MMTEB: Massive Multilingual Text Embedding Benchmark (arXiv 2025)](https://arxiv.org/abs/2502.13595)
- [BGE-M3 paper (arXiv)](https://arxiv.org/abs/2309.07597)
- [voyage-3-large announcement](https://blog.voyageai.com/2024/09/18/voyage-3/)
- [Voyage Multilingual 2 evaluation](https://towardsdatascience.com/voyage-multilingual-2-the-best-multilingual-embedding-model/)
- [Best Chunking Strategies for RAG 2025 (Firecrawl)](https://www.firecrawl.dev/blog/best-chunking-strategies-for-rag-2025)
- [Chunking Strategies (Weaviate)](https://weaviate.io/blog/chunking-large-documents)
- [Hybrid Search + Reranking (Superlinked VectorHub)](https://superlinked.com/vectorhub/hybrid-search-reranking)
- [Citation-Aware RAG (Tensorlake)](https://tensorlake.ai/blog/citation-aware-rag)
- [RAG in 2025: 7 Strategies (Morphik)](https://morphik.ai/blog/rag-strategies-2025)

**Gap-detectie**
- [RAGChecker: Fine-grained Evaluation Framework (Amazon)](https://arxiv.org/abs/2408.08067)
- [Vectara FaithJudge (2025)](https://vectara.com/blog/faithjudge-llm-based-hallucination-detection/)
- [BERTopic documentation](https://maartengr.github.io/BERTopic/index.html)
- [Intercom Fin knowledge gap detection](https://www.intercom.com/blog/fin-knowledge-gap/)
- [GraphRAG guide (Meilisearch 2025)](https://www.meilisearch.com/blog/graphrag)
- [LightRAG: Simple and Fast Retrieval-Augmented Generation](https://arxiv.org/abs/2410.05779)

**Vector DB evaluatie**
- [Qdrant multitenancy en payload-filtering (officieel)](https://qdrant.tech/articles/multitenancy/)
- [Qdrant sparse vectors documentatie](https://qdrant.tech/documentation/concepts/vectors/#sparse-vectors)
- [Weaviate native multi-tenancy (v1.20+)](https://weaviate.io/developers/weaviate/manage-data/multi-tenancy)
- [Weaviate hybrid search architectuur](https://weaviate.io/blog/hybrid-search-explained)
- [pgvector vs Qdrant performance vergelijking](https://tigerdata.io/blog/pgvector-vs-qdrant)
- [BGE-M3 paper -- dense, sparse en ColBERT in 1 model](https://arxiv.org/abs/2309.07597)

**AVG / PII-detectie**
- [EDPB Opinion 28/2024: AI models and personal data](https://www.edpb.europa.eu/system/files/2024-12/edpb_opinion_202428_ai-models_en.pdf)
- [EDPB Guidelines 01/2025: Pseudonymisation](https://www.edpb.europa.eu/system/files/2025-01/edpb_guidelines_202501_pseudonymisation_en.pdf)
- [Microsoft Presidio (GitHub)](https://github.com/microsoft/presidio)
- [Presidio meertalige configuratie](https://microsoft.github.io/presidio/analyzer/languages/)
- [Presidio + GLiNER integratie](https://microsoft.github.io/presidio/samples/python/gliner/)
- [GLiNER: Generalist Model for NER (NAACL 2024)](https://github.com/urchade/GLiNER)

---

## Appendix A: Vector DB evaluatie

> Evaluatiedatum: 2026-03-18. Vereisten: BGE-M3 hybrid search (dense + sparse in 1 model), GDPR-isolatie per tenant, self-hosted, schaal 50 tot 500 organisaties, AnythingLLM als chat interface.

### Vergelijkingstabel

| Criterium | Qdrant (col-per-tenant) | Weaviate | pgvector |
|---|---|---|---|
| BGE-M3 sparse native | Ja | Nee (BM25 only) | Nee |
| Data-isolatie GDPR | Structureel (per collectie) | Structureel (per shard) | Via RLS (applicatielaag) |
| Backup per tenant | Snapshot API native | Ja, per tenant shard | Postgres dump (filterbaar) |
| 500 tenants operationeel | Beheerbaar | Beter (activation/deactivation) | Niet ontworpen voor dit |
| AnythingLLM support | Ja | Ja | Beperkt |
| Operationele eenvoud | Hoog | Middelmatig | Middelmatig |
| Self-hosted rijpheid | Goed | Goed, complexer | Uitstekend |

---

### Qdrant

#### Collection-per-tenant vs. shared collection + payload filter

**Shared collection + payload filter -- waarom afgewezen:**

Qdrant's HNSW-index is een globale graaf over alle vectoren. Filtering werkt na het ophalen van ANN-kandidaten. Bij een sterk selectieve filter (bijv. `org_id = X` terwijl X maar 0,5% van de vectoren bezit), geeft HNSW onvoldoende relevante kandidaten terug voor die tenant -- recall daalt. Qdrant heeft payload pre-filtering via `indexing_threshold`, maar dit schakelt over naar bruteforce scan zodra de gefilterde set klein is. Bij grote datasets wordt dit een prestatieprobleem.

Het grotere bezwaar is veiligheid. Shared collection + payload filter is "security by application logic": elke bug in de filtercode kan leiden tot cross-tenant datalekken. Dit is onaanvaardbaar voor GDPR-isolatie in een multi-tenant SaaS.

**Collection-per-tenant:**

- 500 collecties: geen probleem. Collecties zijn lichtgewicht; de HNSW-graaf wordt alleen in RAM geladen wanneer de collectie actief bevraagd wordt.
- Recall is optimaal per tenant -- de HNSW-graaf is niet vervuild door vectoren van andere orgs.
- Snapshot API werkt per collectie: echte per-tenant backup en restore zonder andere tenants te raken.
- GDPR "right to erasure": `DELETE /collections/org_{uuid}` verwijdert alle data van die org atomair.
- Onboarding nieuwe tenant: één idempotente API-call.

Operationele kanttekening: bij 500+ gelijktijdig actieve tenants met grote datasets is RAM voor HNSW-grafen de primaire bottleneck. Plan op 1-4 GB per actieve grote tenant. Bij een patroon van 500 orgs waarvan 50 gelijktijdig actief zijn, is dit beheersbaar op een enkele node.

#### BGE-M3 sparse vector support

Qdrant ondersteunt sparse vectoren als eerste-klas type (inverted index, niet HNSW), beschikbaar vanaf v1.7. Named vectors in één collectie: `dense` (1024-dim) en `sparse` (BGE-M3 sparse) samen opslaan in hetzelfde punt. De Query API ondersteunt hybrid search met RRF (Reciprocal Rank Fusion) of DBSF als fusie-strategie.

BGE-M3 geeft drie soorten output:

- **Dense** (1024-dim): Qdrant HNSW -- native, geen aanpassing nodig
- **Sparse** (SPLADE-stijl, vocabulary-size): Qdrant inverted index -- native
- **ColBERT multi-vector** (MaxSim scoring): niet native ondersteund in Qdrant; niet vereist voor dit platform

#### Backup/restore per tenant

- `POST /collections/org_{uuid}_help_center/snapshots` -- snapshot per tenant
- Restore op een andere Qdrant-instantie zonder andere tenants te beïnvloeden
- GDPR-export per org: export snapshot, verwijder collectie met één API-call

#### Bekende zwakheden

- **Geen built-in RBAC**: Qdrant heeft geen gebruikersauthenticatie per collectie. Toegangscontrole -- welke applicatieservice welke collectie mag benaderen -- moet volledig in de applicatielaag worden geïmplementeerd.
- **Distributed mode**: functioneel maar minder volwassen dan enterprise-alternatieven. Sharding en replicatie vereisen expliciete planning.
- **Geen ACID-transacties**: point updates zijn atomair; complexe transacties over meerdere punten niet. Voor een RAG-platform is dit doorgaans geen beperking.
- **Operationele tooling**: geen native admin-UI voor productiegebruik; monitoring via Prometheus/Grafana werkt goed.

---

### Weaviate -- niet gekozen

**Primaire reden: BGE-M3 sparse vector support ontbreekt.**

Weaviate's hybrid search combineert BM25 (keyword-gebaseerd, sparse) met dense embeddings. BM25 is term-frequency gebaseerd: het telt hoe vaak een zoekterm voorkomt in een document. BGE-M3's sparse output is SPLADE-stijl: een geleerde sparse vector over het volledige vocabulaire van het taalmodel, waarbij gewichten via training worden bepaald. Dit zijn fundamenteel andere representaties.

Weaviate heeft geen inverted index voor arbitraire geleerde sparse vectoren. In de praktijk betekent dit: je kunt BGE-M3 dense opslaan in Weaviate en BM25 als sparse component gebruiken, maar dan wordt BGE-M3's sparse output nooit benut. Je verliest het voornaamste voordeel van BGE-M3 ten opzichte van losse dense-only modellen.

**Waar Weaviate beter zou zijn geweest:**

- Native multi-tenancy met tenant activation/deactivation (v1.20+): inactieve tenants worden naar disk gezet en verbruiken geen RAM. Bij 500 orgs waarvan de meeste infrequent actief zijn, scheelt dit aanzienlijk in resource gebruik. Qdrant heeft dit niet natively.
- Tenant-shards zijn geïntegreerd in het datamodel; geen aparte routing in de applicatielaag nodig.

**Overige zwakheden:**

- Modules-systeem: Weaviate werkt met vectorizer-modules die apart moeten worden gedeployed. Complexere self-hosted setup vergeleken met Qdrant's single binary.
- Breaking changes: Weaviate heeft historisch relatief veel breaking API-wijzigingen gehad tussen major versies.
- GraphQL API wordt uitgefaseerd richting REST/gRPC -- vroege adoptie zou nu al migratieoverhead vereisen.

---

### pgvector -- niet gekozen

**Primaire reden: geen sparse vector indexering.**

pgvector slaat vectoren op als Postgres-kolommen maar heeft geen inverted index voor geleerde sparse vectoren. BGE-M3 sparse output kan niet efficiënt worden geïndexeerd. Hybrid search is te realiseren via `tsvector` full-text search gecombineerd met vectorgelijkenis, maar dat is BM25-equivalent, niet BGE-M3 sparse -- hetzelfde probleem als bij Weaviate.

**Schaalgrens vergeleken met Qdrant:**

| Vectorcount | pgvector | Qdrant |
|---|---|---|
| < 500K | Prima, overhead acceptabel | Functioneel maar overkill |
| 500K - 2M | Merkbare latentiestijging bij complexe queries | Goed, in-memory HNSW |
| > 2M | HNSW-build tijd en RAM worden problematisch | Ontworpen voor dit |

**Zinvol als tussenoplossing?**

Nee voor dit platform. BGE-M3 hybrid search (dense + sparse) is een harde vereiste vanaf dag 1. pgvector als start en later migreren naar Qdrant betekent: re-embedding van alle documenten, re-indexering, downtime-planning en gegevensmigratiewerk. Dit levert geen voordeel op ten opzichte van direct beginnen met Qdrant.

pgvector is wél zinvol voor: teams met Postgres als kerncompetentie, use cases zonder sparse search, datasets die permanent onder de 500K vectoren blijven.

---

### Conclusie

Qdrant met collection-per-tenant is de enige keuze die aan de drie harde vereisten tegelijk voldoet:

1. **BGE-M3 native sparse**: alleen Qdrant ondersteunt geleerde sparse vectoren met een inverted index.
2. **Structurele GDPR-isolatie**: collection-per-tenant garandeert dat een filterbug nooit kan leiden tot cross-tenant datalekken.
3. **Operationeel beheerbaar bij 500 orgs**: 500 collecties is geen probleem voor Qdrant; met goede RAM-planning voor HNSW-grafen schaalbaar naar 500+ actieve tenants.

Weaviate scoort beter op tenant activation/deactivation (RAM-efficiëntie bij inactieve tenants) maar valt af op BGE-M3 sparse. pgvector valt af op zowel sparse als schaal.

---

## Appendix B: Unified Ingest API

> Evaluatiedatum: 2026-03-18. Scope: de centrale schrijfinterface die alle brontypen normaliseert naar Qdrant-vectoren.

### Architectuurprincipe

De unified ingest API is de **enige schrijfinterface naar Qdrant**. Adapters leveren tekst + metadata af aan dit endpoint; de API verzorgt de rest. Dit patroon is al in productie in `klai-research/research-api`: dezelfde source-type router, dezelfde achtergrondverwerking via FastAPI `BackgroundTask`, en dezelfde docling-serve als document-parser. Het verschil is de target store (pgvector daar, Qdrant + BGE-M3 hier) en de enrichment-stappen (Contextual Retrieval + HyPE).

---

### Source type mapping

| source_type | Parser | Chunking | Enrichment |
|---|---|---|---|
| `helpdesk_json` | Helpdesk-adapter (Instructor + Pydantic) | `json_item_to_text()` (sectie 6.3) | Geen |
| `document` | docling-serve | HybridChunker (docling-serve `/v1/chunk/hybrid/file`) | Contextual Retrieval + HyPE |
| `web_page` | Crawl4AI (levert markdown) | HybridChunker (docling-serve `/v1/chunk/hybrid/file`, markdown als input) | Contextual Retrieval + HyPE |
| `notebook` | Direct (al markdown) | HybridChunker (docling-serve `/v1/chunk/hybrid/file`, markdown als input) | HyPE optioneel |

**Waarom HybridChunker i.p.v. LangChain-splitters:** De conversie `docling-serve → markdown → MarkdownHeaderSplitter + RecursiveCharacterTextSplitter` verliest structuurmetadata die Docling's eigen chunkers bewaren: bounding boxes en paginanummers per element, character spans, element type labels (`TABLE`, `PICTURE`, `FORMULA`, `SECTION_HEADING`), captions correct gekoppeld aan hun tabel of figuur, en tabel-integriteit (HybridChunker splitst nooit mid-table). Bovendien is HybridChunker tokenisatie-bewust: het respecteert BGE-M3's token-limiet (8192 tokens) en merget te kleine chunks. `RecursiveCharacterTextSplitter` splitst op karakters, niet tokens, wat leidt tot onderbezette of oversized chunks.

De `/v1/chunk/hybrid/` endpoints zijn beschikbaar in docling-serve vanaf september 2025 (PR #353). Elke chunk in de response bevat `text`, `headings`, `captions`, `page_numbers`, `doc_items` (JSON Pointer refs) en `num_tokens`. Bekende bug: bij `/v1/chunk/hierarchical/file` worden verkeerde chunker-opties gebruikt (issue #486); gebruik de `/source` variant of HybridChunker.

**Wanneer geen enrichment:** Contextual Retrieval en HyPE (sectie 6.0, fase 2 en 3) zijn ontworpen voor kennisbankinhoud waarbij chunks context missen zonder hun omringende document. Helpdesk JSON is al gedistilleerd door de extractor -- `problem_summary`, `information_sought` en `steps_taken` zijn al zelfstandige teksteenheden. Contextual prepend voegt hier niets toe.

---

### FastAPI endpoint

```python
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from enum import Enum
from typing import Optional
import uuid

app = FastAPI()

class SourceType(str, Enum):
    helpdesk_json = "helpdesk_json"
    document      = "document"       # PDF, DOCX, XLSX, PPTX -- via docling-serve
    web_page      = "web_page"       # al gecrawled naar markdown via Crawl4AI
    notebook      = "notebook"       # markdown

class Visibility(str, Enum):
    internal = "internal"
    external = "external"
    public   = "public"

class IngestRequest(BaseModel):
    source_type:     SourceType
    org_id:          str
    visibility:      Visibility = Visibility.internal
    content:         str                  # tekst, markdown of JSON-string
    source_metadata: dict = {}            # titel, url, article_id, lang, etc.

class IngestResponse(BaseModel):
    ingest_id: str
    status:    str = "accepted"

class IngestStatus(BaseModel):
    ingest_id:     str
    status:        str                    # "processing" | "ready" | "error"
    chunks_count:  Optional[int] = None
    error_message: Optional[str] = None

@app.post("/ingest", response_model=IngestResponse)
async def ingest(request: IngestRequest, background_tasks: BackgroundTasks):
    ingest_id = "ing_" + uuid.uuid4().hex[:24]
    background_tasks.add_task(run_ingest_pipeline, ingest_id, request)
    return IngestResponse(ingest_id=ingest_id)

@app.get("/ingest/{ingest_id}", response_model=IngestStatus)
async def get_ingest_status(ingest_id: str):
    # Status ophalen uit ingest-registry
    ...
```

**Geen file upload op het ingest endpoint.** De document-adapter roept docling-serve's `/v1/chunk/hybrid/file` endpoint aan -- docling-serve verzorgt zowel parsing als chunking. De adapter levert de resulterende chunks als losse `content`-items aan het ingest endpoint. Het ingest endpoint verwerkt altijd tekst per chunk, nooit raw binary.

---

### Pipeline

```
IngestRequest
  (source_type, org_id, visibility, content, source_metadata)
      |
      v
  ROUTER (per source_type)
      |
      +-- helpdesk_json --> json_item_to_text()
      |                     (geen chunking: één item = één vector)
      |
      +-- document / web_page / notebook
                --> docling-serve /v1/chunk/hybrid/    [HybridChunker, token-aware]
                --> add_context() per chunk            [Contextual Retrieval, sectie 6.0 fase 2]
                --> generate_questions() per chunk     [HyPE, sectie 6.0 fase 3]
      |
      v
  BGE-M3 embedding (FlagEmbedding, dense 1024-dim + sparse SPLADE)
      |
      v
  Qdrant upsert
    collection: org_{org_id}_{content_type}            [collection-per-tenant, Appendix A]
    payload:    source_metadata + visibility + lang + source_type
```

**Embedding: FlagEmbedding, niet TEI.** HuggingFace Text Embeddings Inference (TEI) ondersteunt BGE-M3 sparse niet. TEI's `/embed_sparse` endpoint vereist een `ForMaskedLM` architectuur (BERT-family); BGE-M3 gebruikt `BGEM3Model` -- een fundamenteel andere architectuur. Dit is een bekende, onopgeloste bug (TEI issue #289, open sinds juni 2024). FastEmbed ondersteunt BGE-M3 sparse ook niet (alleen SPLADE en BM25 varianten).

De enige werkende weg voor BGE-M3 dense + sparse in één pass:

```python
from FlagEmbedding import BGEM3FlagModel

model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)
output = model.encode(texts, return_dense=True, return_sparse=True)

dense_vecs    = output['dense_vecs']       # shape (N, 1024), float32
sparse_vecs   = output['lexical_weights']  # list[dict[int, float]] -- token_id: weight

# Omzetten naar Qdrant SparseVector
from qdrant_client.models import SparseVector
sparse_qdrant = SparseVector(
    indices=list(sparse_vecs[0].keys()),
    values=list(sparse_vecs[0].values()),
)
```

TEI mag nog steeds worden ingezet voor BGE-M3 **dense** embeddings (als standaard XLM-RoBERTa CLS embedding). Als je TEI al gebruikt voor dense, voeg dan FlagEmbedding toe als aparte stap voor sparse -- of gebruik FlagEmbedding voor beide vectoren in één pass.

---

### Qdrant collection naamgeving

| source_type | Collection |
|---|---|
| `helpdesk_json` | `org_{org_id}_extracted_knowledge` |
| `document` / `web_page` | `org_{org_id}_help_center` |
| `notebook` | `org_{org_id}_notebooks` |

Bij org-onboarding worden alle drie collections aangemaakt via `create_tenant_collection()` (zie sectie 6.1). Idempotent, zodat heruitvoering veilig is.

---

### Toegangsbeleid (visibility)

Het ingest endpoint slaat `visibility` op als payload-veld op elk Qdrant-punt. De retrieval-laag (Haystack pipeline, zie sectie 6.6) filtert altijd op `visibility` naast de collection-routing op `org_id`. Dit geeft twee onafhankelijke lagen van toegangscontrole:

1. **Structureel**: collection-per-tenant -- org-isolatie op databaseniveau
2. **Filtergebaseerd**: `visibility` binnen een collection -- interface-isolatie

`allowed_visibility` per interface -- zie sectie 6.1.

---

### Relatie tot klai-research

`klai-research/research-api` implementeert hetzelfde patroon voor de research-use case, met pgvector als target store. De twee systemen zijn onafhankelijk; de unified ingest API hier schrijft uitsluitend naar Qdrant. Eventuele hergebruikte code (chunker, docling client, achtergrondtaak-patroon) kan worden geextraheerd naar een gedeelde interne library zodra beide systemen productie-rijp zijn.

---

## Knowledge Base exposure layer

### Architectuur: Git + Markdown + block editor

```
Gitea/Forgejo (self-hosted Git)
        |
        |  API (lees/schrijf markdown bestanden)
        |
[BlockNote editor]  -->  serialize to markdown  -->  commit via Gitea API
        |
   Gitea webhook op commit
        |
   RAG ingestion pipeline  -->  Qdrant (nieuwe versie = nieuwe input)
        |
[Next.js KB site]  -->  leest markdown via Gitea API of git clone
```

**Anonieme editing:** Gitea ondersteunt public repos met write access via tokens. Voor publieke documenten wordt een tijdelijk of beperkt-scope token per sessie gegenereerd -- geen gebruikersaccount vereist.

**Versiehistorie:** ingebouwd via Git. Diffs zijn zichtbaar in de KB-interface ("dit artikel werd gewijzigd op...").

**Multi-tenant:** een Gitea-organisatie per tenant, eigen repo's per tenant.

### Eigenschappen

| Eigenschap | Invulling |
|---|---|
| Storage | Markdown-bestanden in Git (Gitea/Forgejo, self-hosted) |
| Versiecontrole | Native Git -- elke wijziging is een commit |
| Editor | BlockNote (block-based, gebruikersvriendelijk) |
| Serialisatie | BlockNote JSON <-> Markdown (BlockNote markdown serializer) |
| Anonieme editing | Tijdelijk Gitea-token per sessie voor aangewezen publieke docs |
| Human in the loop | Wijzigingen in Git zijn direct input voor RAG re-ingestion |
| Multi-tenant | Gitea-organisatie per tenant, repo's per tenant |
| Auth | Zitadel OIDC voor geauthenticeerde editors |
| KB-site | Next.js leest markdown via Gitea API, rendert via BlockNote viewer (SSR) |
| RAG-koppeling | Gitea webhook op commit triggert ingest pipeline naar Qdrant |

---

## Appendix C: Chat widget SDK evaluatie

> Onderzoeksdatum: 2026-03-18
> Context: self-hosted white-label AI chat widget in TypeScript/React, streamt responses van eigen RAG backend (custom API endpoint). Vereiste: open source licentie.

### Evaluatiecriteria

1. Werkt het met een custom backend (niet gebonden aan een specifieke LLM-provider)?
2. Hoe ziet de client-side streaming story eruit (hooks, components, handmatig)?
3. Framework-agnostisch of React-only?
4. Bundle-impact voor een embeddable widget?
5. Licentie (MIT, Apache, commercieel)?
6. Vendor lock-in risico?

---

### 1. Vercel AI SDK (`@ai-sdk/react`)

**Apache 2.0 | ~15-20 kB min+gzip | ~40k GitHub stars**

- **Custom backend:** First-class. Implementeer het Data Stream Protocol op je eigen server (elke taal), wijs `useChat({ api: '/jouw/endpoint' })` ernaar. Geen provider-package vereist.
- **Streaming:** `useChat` hook beheert SSE-parsing, message state, optimistic updates, abort signals. AI SDK 6 gebruikt standaard SSE, debuggable in browser DevTools.
- **Framework:** React, Vue, Svelte, Solid -- niet gebonden aan React.
- **Bundle:** Alleen `@ai-sdk/react` nodig (~15-20 kB). Geen provider-package vereist voor custom backends.
- **Lock-in:** Laag. Vercel AI Gateway (betaald) is volledig optioneel en irrelevant voor een custom backend. RSC-specifieke features creëren Next.js-koppeling alleen als je ze gebruikt.

---

### 2. LangChain.js

**MIT | Zwaar | ~14k stars**

- **Custom backend:** Verkeerde laag. Het is een server-side orchestratie-library. De `useStream` hook uit `@langchain/langgraph-sdk/react` is strak gekoppeld aan de LangGraph Server API -- geen generiek custom endpoint.
- **Streaming:** `useStream` is LangGraph-specifiek. LangChain-documentatie verwijst zelf naar assistant-ui of CopilotKit voor chat-UI's.
- **Framework:** React (UI-laag). Core is server-side.
- **Bundle:** Zwaar. Niet geschikt voor een widget.
- **Lock-in:** Hoog -- de UI-hook vereist een LangGraph-deployment.

**Conclusie: verkeerde laag. Overslaan.**

---

### 3. LlamaIndex.TS (`@llamaindex/chat-ui`)

**MIT | Additief op AI SDK**

- **Custom backend:** Ja -- maar het is een dunne UI-wrapper over `@ai-sdk/react`'s `useChat`. Backend moet nog steeds het Vercel AI SDK Data Stream Protocol spreken.
- **Streaming:** Volledig gedelegeerd aan `useChat`. Geen extra streaming-logica.
- **Framework:** React-only.
- **Bundle:** Additief op Vercel AI SDK + shadcn/Tailwind.
- **Lock-in:** Laag, maar de package had ~6 maanden geen updates (peildatum maart 2026).

**Conclusie: slechts een UI-laag boven op de AI SDK. Niet zelfstandig de moeite waard; onderhoudsritme is traag.**

---

### 4. CopilotKit

**MIT | ~200 kB+ gecombineerd | ~28k stars**

- **Custom backend:** Vereist `@copilotkit/runtime` op de server. Je RAG-backend moet worden ingepakt in of vervangen door die runtime.
- **Streaming:** Transparant via AG-UI (open SSE-gebaseerd protocol). `useAgent` / `useCopilotChat` hooks.
- **Framework:** React + Angular.
- **Bundle:** Zwaar. Het is een volledig agentisch framework (generative UI, state sync, human-in-the-loop). Te veel voor een widget.
- **Lock-in:** Gemiddeld. AG-UI protocol is open, maar het gebruikelijke pad bindt je backend aan `@copilotkit/runtime`.

**Conclusie: uitstekend voor volledige SaaS-agentische features; te zwaar en te eigenzinnig voor een lean embeddable widget.**

---

### 5. assistant-ui (`@assistant-ui/react`)

**MIT | Matig, tree-shakeable | ~8k stars | YC-backed**

- **Custom backend:** Sterkste story hier. `LocalRuntime` adapter accepteert een async generator van elke fetch-aanroep:

```ts
useLocalRuntime({
  async run({ messages, abortSignal }) {
    const res = await fetch('/jouw/rag/endpoint', {
      method: 'POST',
      signal: abortSignal,
      body: JSON.stringify({ messages })
    })
    return streamFromResponse(res) // async generator
  }
})
```

Alternatief: `useVercelUseChatRuntime` bridget `@ai-sdk/react`'s `useChat` als je backend dat protocol spreekt.

- **Streaming:** Runtime adapter + async generator. Library beheert UI-updates, auto-scroll, branching, retries.
- **Framework:** React-only, TypeScript-first.
- **Bundle:** Composable primitives, tree-shakeable. Alleen importeren wat je nodig hebt. Afhankelijk van shadcn/ui + Tailwind.
- **Lock-in:** Zeer laag. Optionele `assistant-cloud` voor thread-persistentie is één env-variabele -- geen code-aanpassing nodig om het over te slaan.

**Aandachtspunten:** v0.x API-churn, kleinere community dan AI SDK, vereist Tailwind/shadcn build-setup.

---

### 6. Raw fetch + ReadableStream / SSE

**Geen dependencies | 0 kB overhead**

- **Custom backend:** Volledige controle.
- **Streaming:** Handmatige `ReadableStream` reader-loop. Zelf te bouwen: SSE/NDJSON chunk-parsing (partial reads), message history state, loading/error state, abort bij unmount, optimistic UI, auto-scroll, ARIA live regions, retry-logica, markdown rendering.
- **Framework:** Agnostisch. Werkt in elke JS-omgeving.
- **Bundle:** Alleen eigen code (~2-4 kB voor een minimale hook).
- **Lock-in:** Nul.

Een productie-waardige custom hook is circa 200-400 regels TypeScript voor visuele afwerking begint. Edge cases (partial chunk-grenzen, abort race conditions, accessibility) zijn niet triviaal.

---

### Vergelijkingstabel

| Optie | Custom backend | Streaming | Frameworks | Est. bundle | Licentie | Lock-in |
|---|---|---|---|---|---|---|
| Vercel AI SDK (`@ai-sdk/react`) | Ja, first-class | `useChat` hook, SSE | React, Vue, Svelte, Solid | ~15-20 kB | Apache 2.0 | Laag |
| LangChain.js | Nee (LangGraph-only UI hook) | `useStream` (LangGraph) | React (UI) | Zwaar | MIT | Hoog |
| LlamaIndex chat-ui | Ja (wikkelt AI SDK) | Delegeert aan `useChat` | React | Additief op AI SDK | MIT | Laag-gemiddeld |
| CopilotKit | Ja (vereist server runtime) | `useAgent` / `useCopilotChat` | React, Angular | ~200 kB+ | MIT | Gemiddeld |
| assistant-ui | Ja, `LocalRuntime` adapter | Async generator | React | Matig, tree-shakeable | MIT | Zeer laag |
| Raw fetch + SSE | Ja, volledige controle | Handmatige loop | Alles | 0 kB | Geen | Nul |

---

### Aanbeveling

**Primair: `@ai-sdk/react` als data-laag**

Voor een streaming embeddable widget met een custom backend is `useChat` gericht op je eigen endpoint de meest battle-tested en kleinste optie. Apache 2.0, geen Vercel-account nodig, geen Next.js vereist. UI schrijf je zelf. Implementeer het [Data Stream Protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol) op je backend -- een eenvoudig SSE-formaat, implementeerbaar in elke servertaal in onder de 50 regels.

**Aanvulling als je pre-built UI-components wil: assistant-ui bovenop `@ai-sdk/react`**

Gebruik `useVercelUseChatRuntime` om `useChat` te bridgen naar assistant-ui's runtime, en gebruik alleen de composable primitives die je nodig hebt. MIT-licentie, geen vereiste cloud-diensten, `LocalRuntime` werkt ook als je de AI SDK volledig wil omzeilen.

**Niet gebruiken voor dit doel:** LangChain.js (verkeerde laag), CopilotKit (te zwaar, server runtime vereist), LlamaIndex chat-ui (traag onderhouden, toch slechts een wrapper).

**Raw fetch alleen als:** je een non-React embed nodig hebt (vanilla JS, web component), of je totale bundle-budget onder de 5 kB moet blijven.

---

## Appendix D: MDX voor de knowledge exposure layer

> Onderzoeksdatum: 2026-03-19. Scope: haalbaarheid van MDX (Markdown + JSX) als opslagformaat voor de KB-exposure layer, gegeven de huidige stack (Gitea, BlockNote, Next.js SSR, docling-serve HybridChunker, BGE-M3 + Qdrant).

### Wat MDX toevoegt voor een B2B KB

MDX compileert Markdown + JSX naar een React component. De concrete wins voor een kennisbank:

| Feature | Nut voor B2B KB | Alternatief zonder MDX |
|---|---|---|
| Tabbed content | Hoog -- installatie-instructies per platform (npm/yarn/Linux/Windows) | remark-directive |
| Custom callouts | Gemiddeld -- warning/info/tip met styling | remark-directive |
| Herbruikbare data-componenten | Hoog -- `<PricingTable />`, `<SupportedRegions />` die live data rendert | Niet equivalent |
| Interactieve embeds | Hoog als relevant -- API explorers, live code | Niet equivalent |

**Conclusie voor puur proza + code blocks + tabellen:** MDX voegt niets toe. De toegevoegde waarde zit uitsluitend in interactieve of herbruikbare componenten.

---

### Three breaking points in de huidige stack

#### 1. BlockNote serialiseert niet naar MDX

BlockNote serialiseert naar CommonMark markdown en JSON (intern block-formaat). Er is geen native MDX serializer en het staat niet op de roadmap. Om MDX als opslagformaat te gebruiken zijn twee opties:

**Optie A: Custom BlockNote serializer**

Bouw een mapping van BlockNote block-types naar JSX component calls:

```typescript
// callout block → <Callout type="warning">...</Callout>
// tabs block    → <Tabs><Tab label="npm">...</Tab></Tabs>
```

Geschat werk: 1-2 weken voor een kleine component set (Callout, Tabs, Steps). Vereist ongoing onderhoud bij elke nieuwe component.

**Optie B: Code-editor als schrijfomgeving**

Schrijvers editen MDX als broncode (Monaco/CodeMirror) met een live preview naast elkaar. Dit is hoe Mintlify's editor werkt. Minder toegankelijk voor niet-technische gebruikers.

#### 2. De RAG pipeline breekt op JSX

docling-serve's HybridChunker verwerkt Markdown semantisch. JSX syntax begrijpt het niet:

```mdx
import { Tabs } from '@/components/Tabs'  <!-- wordt als code block gezien -->

<Tabs>
  <Tab label="yarn">                       <!-- content hieronder gaat verloren of wordt verkeerd gechunkt -->
    Run `yarn add @klai/sdk`
  </Tab>
</Tabs>
```

**Concreet retrieval-probleem:** als een gebruiker vraagt "hoe installeer ik dit met yarn?", staat het antwoord in `<Tab label="yarn">`. Zonder expliciete preprocessing bereikt dit de vector store niet, of niet met de juiste context.

**Vereiste preprocessing stap** (vóór doorgeven aan HybridChunker):

```python
import re

def flatten_mdx_for_rag(mdx: str) -> str:
    # Verwijder import-statements
    mdx = re.sub(r'^import\s+.*?;?\s*$', '', mdx, flags=re.MULTILINE)
    # Flatten tab-componenten naar markdown secties
    mdx = re.sub(
        r'<Tab\s+label=["\']([^"\']+)["\']>\s*(.*?)\s*</Tab>',
        r'### \1\n\n\2',
        mdx, flags=re.DOTALL
    )
    # Verwijder overige JSX tags, bewaar inner tekst
    mdx = re.sub(r'<[A-Z][A-Za-z]*[^>]*/>', '', mdx)         # self-closing
    mdx = re.sub(r'<[A-Z][A-Za-z]*[^>]*>', '', mdx)          # opening
    mdx = re.sub(r'</[A-Z][A-Za-z]*>', '', mdx)               # closing
    return mdx.strip()
```

Deze stap moet worden toegevoegd in de Gitea webhook handler, vóór de call naar docling-serve. De component-context (welke tab) wordt bewaard via de heading die de regex genereert.

#### 3. Server-side code execution (next-mdx-remote)

`@next/mdx` werkt alleen met lokale bestanden op build-time en is daarmee niet bruikbaar voor content die runtime wordt opgehaald van Gitea. De enige compatibele library voor de huidige architectuur is `next-mdx-remote` (v4+, RSC-compatible).

`next-mdx-remote` compileert MDX naar executable JavaScript op de server. Als een tenant willekeurige JSX kan schrijven, is dit een server-side code execution surface.

**Verplichte mitigation:**

```typescript
import { compileMDX } from 'next-mdx-remote/rsc'

const { content } = await compileMDX({
  source: mdxFromGitea,
  // Alleen whitelisted components -- geen import statements van user content
  components: { Callout, Tabs, Tab, Steps, Step },
  options: { parseFrontmatter: true }
})
```

Geen `import`-statements toestaan vanuit user-authored content. Alleen pre-registered components beschikbaar stellen. Security review vereist vóór productie met tenant-authored MDX.

---

### Alternatief: markdown + remark-directive

Frameworks als Starlight, VitePress en Docusaurus gebruiken `remark-directive` bewust als alternatief voor raw MDX. De syntax is CommonMark-adjacent en vermijdt alle drie breaking points:

```markdown
::callout{type="warning"}
Doe dit niet zonder backup.
::

:::tabs
::tab{label="npm"}
`npm install @klai/sdk`
::
::tab{label="yarn"}
`yarn add @klai/sdk`
::
:::
```

| Aspect | MDX | remark-directive |
|---|---|---|
| BlockNote serializer | Custom bouwen | Eenvoudiger te mappen |
| RAG pipeline | Preprocessing vereist | Geen aanpassing nodig |
| Gitea rendering | Raw text (onleesbaar) | Leesbaar als plain text |
| Security (next-mdx-remote) | Code execution surface | Geen -- remark plugins, geen JS eval |
| Herbruikbare live components | Ja | Nee |
| Tabbed content / callouts | Ja | Ja |

`remark-directive` geeft 80% van de KB-waarde van MDX met 10% van de complexiteit. De enige feature die niet equivalent is: herbruikbare data-driven componenten (`<PricingTable />` die live data rendert).

---

### Aanbeveling

**Gebruik remark-directive als** het gebruik case primair gaat om tabbed installatie-instructies en gekleurde callouts. Dit werkt nu, zonder aanpassingen aan de RAG pipeline, de editor, of de security-review vereisten.

**Investeer in MDX als** er een concrete behoefte is aan interactieve embedded components of herbruikbare data-driven content blocks. De sequentie:

1. `next-mdx-remote` toevoegen aan de Next.js renderer; component whitelist definiëren; geen `import` van user content
2. MDX preprocessing stap toevoegen in de Gitea webhook handler (vóór docling-serve): strip imports, flatten tab/step componenten naar markdown secties
3. BlockNote-naar-MDX serializer bouwen voor de specifieke component set
4. Security review op de compilatie-pipeline vóór productie met tenant-authored content

Gitea toont `.mdx` bestanden als plain text -- dit is een bewuste afweging bij de keuze voor MDX.

---

## Appendix E: Publication Layer -- productdefinitie

> Vastgelegd: 2026-03-19. Scope: standalone B2B knowledge base publicatieplatform. Losgekoppeld van de Knowledge Intelligence pipeline (gap-detectie, extractie); koppeling via webhook wordt later toegevoegd.

### Positionering

De Publication Layer is een zelfstandig B2B SaaS-product waarmee organisaties meerdere knowledge bases kunnen beheren en publiceren. Inhoud wordt opgesteld in markdown, opgeslagen in Git, en gepubliceerd als een leesbare kennisbank op een eigen (sub)domein.

De koppeling met de Knowledge Intelligence pipeline (Product 1: helpdesk-extractie, gap-detectie, redactie-inbox) wordt later toegevoegd via een webhook op elke Git-commit.

---

### Tenant- en KB-model

```
Organisatie (bijv. Voys)
  └── Knowledge base: "Help center"         ← public
        └── mappen en artikelen
  └── Knowledge base: "Interne procedures"  ← private
        └── mappen en artikelen
```

- Een organisatie heeft een eigen subdomain en kan meerdere knowledge bases aanmaken.
- Elke knowledge base is onafhankelijk van de andere: eigen structuur, eigen zichtbaarheid, eigen URL-pad.

---

### URL-structuur en custom domains

**Standaard:**

```
{org}.getklai.com/{kb-slug}/
```

Bijvoorbeeld: `voys.getklai.com/help-center/` en `voys.getklai.com/intern/`

**Custom domain (later instelbaar per organisatie):**

Klant voegt een CNAME toe: `docs.voys.nl → voys.getklai.com`

Caddy (public-01) pikt het hostname op, routeert naar de juiste org, en verzorgt SSL via Let's Encrypt automatisch. De KB-paden blijven gelijk:

```
docs.voys.nl/help-center/
docs.voys.nl/intern/
```

---

### Inhoudsmodel

**Per artikel -- YAML frontmatter:**

```yaml
---
title: Quick start
description: Up and running in five minutes.
edit_access: org          # "org" (iedereen) of lijst van user-IDs
---

# Quick start
...
```

**Per map -- `_meta.yaml`:**

```yaml
title: Getting started
order:
  - quick-start
  - installation
  - faq
```

Mappenstructuur = navigatiehiërarchie. De `_meta.yaml` definieert weergavenaam en volgorde van de kinderen. Geen aparte navigatieconfig nodig.

---

### Navigatiestructuur

Nextra-stijl: mappenstructuur is de navigatieboom, `_meta.yaml` per map bepaalt volgorde en weergavenaam. Voorbeeld:

```
help-center/
  _meta.yaml                       ← ["getting-started", "integrations", "billing"]
  getting-started/
    _meta.yaml                     ← ["quick-start", "installation"]
    quick-start.md
    installation.md
  integrations/
    _meta.yaml
    zapier.md
    api.md
  billing/
    invoices.md
```

Volgorde aanpassen in de editor = drag-and-drop in de zijbalk, schrijft `_meta.yaml` terug.

---

### Editor

Twee manieren om inhoud aan te leveren:

1. **BlockNote in-browser editor** -- block-gebaseerd, serialiseert naar CommonMark markdown + YAML frontmatter. Toegankelijk voor niet-technische gebruikers.
2. **Markdown upload** -- upload een `.md` bestand, wordt direct in de juiste map geplaatst.

Beide schrijven naar dezelfde Git-opslag.

---

### Toegangsmodel

**Leestoegang -- per knowledge base:**

| Instelling | Wie kan lezen |
|---|---|
| `public` | Iedereen (anoniem internet) |
| `private` | Alleen leden van de organisatie (ingelogd via Zitadel) |

**Schrijftoegang -- per artikel:**

| Instelling | Wie kan bewerken |
|---|---|
| `edit_access: org` | Iedereen in de organisatie (standaard) |
| `edit_access: [user-id, ...]` | Alleen de genoemde leden |

Instelling staat in de frontmatter van het artikel. De editor toont een toegangsinstellingen-paneel vergelijkbaar met Notion.

---

### Auth

Zitadel (al in productie op core-01). Organisatie-leden authenticeren via OIDC. Voor publieke KB's is lezen anoniem; schrijven vereist altijd authenticatie.

---

### Toekomstige uitbreidingen (buiten scope v1)

| Feature | Wanneer |
|---|---|
| Real-time samenwerken in de editor | V2 |
| Webhook op commit → Knowledge Intelligence ingest API | Zodra Product 1 bestaat |
| Custom domain instelbaar via UI | Na MVP |
| Volledige sitesearch binnen een KB | Na MVP |
