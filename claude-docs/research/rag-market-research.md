# RAG / Knowledge AI Market Research

> Research date: 2026-03-24
> Context: Klai Knowledge platform — GTM positioning, website strategie, pricing
> Triggered by: Vraag over privacygerichte RAG systemen + product positioning Klai Knowledge

---

## Inhoud

| § | Sectie |
|---|---|
| 1 | [Marktlandschap: bekende RAG/knowledge AI platforms](#1-marktlandschap) |
| 2 | [Wie koopt dit en waarom](#2-wie-koopt-dit-en-waarom) |
| 3 | [Welke sectoren winnen](#3-welke-sectoren-winnen) |
| 4 | [Hoe de concurrentie verkoopt](#4-hoe-de-concurrentie-verkoopt) |
| 5 | [Marktcijfers](#5-marktcijfers) |
| 6 | [Gaps in de markt — Klai's positie](#6-gaps-in-de-markt) |

---

## 1. Marktlandschap

### Puur RAG-infrastructuur (frameworks & vector DBs)

| Platform | Type | Privacy/PII focus | Website |
|---|---|---|---|
| [LangChain](https://www.langchain.com) | RAG framework | Generiek | Oranje/zwart |
| [LlamaIndex](https://www.llamaindex.ai) | RAG framework | Generiek | Blauw/paars |
| [Haystack](https://haystack.deepset.ai) (deepset) | RAG framework | Generiek | Donker |
| [Weaviate](https://weaviate.io) | Vector DB + RAG | HIPAA, ISO 27001, self-hosting | Groen |
| [Pinecone](https://www.pinecone.io) | Vector DB + RAG | HIPAA, SOC2, GDPR | Groen |
| [Qdrant](https://qdrant.tech) | Vector DB | Generiek | Rood/donker |
| [Chroma](https://www.trychroma.com) | Vector DB | Generiek | Paars/donker |
| [Milvus](https://milvus.io) / [Zilliz](https://zilliz.com) | Vector DB | Generiek | Blauw |

### Specifiek privacy/PII-gericht

| Platform | Type | Privacy/PII focus |
|---|---|---|
| [Vectara](https://www.vectara.com) | RAG-as-a-Service | Kernpitch: nooit training op klantdata; HIPAA, healthcare, finance, government |
| [Nuclia](https://nuclia.com) | RAG-as-a-Service | EU-gebaseerd, "100% privacy", finance + legal, on-premises optie |
| [OPAQUE](https://www.opaque.co) | Confidential RAG | Hardware-level TEEs — data blijft versleuteld tijdens verwerking |
| [Limina](https://www.getlimina.ai) (v/h Private AI) | PII detectie + RAG | PII/PHI redactie voor healthcare, finance, insurance, contact centers |
| [Protecto](https://www.protecto.ai) | PII detectie | HIPAA/GDPR policies, PHI masking, enterprise |

### Enterprise knowledge platforms (breder dan RAG)

| Platform | Type | Bijzonderheid |
|---|---|---|
| [Glean](https://www.glean.com) | Work AI / enterprise search | $208M ARR, 100+ connectoren, permissions-aware |
| [Sinequa](https://www.sinequa.com) | Enterprise agentic AI | Sterk in life sciences; verkocht aan ChapsVision 2024 |
| [Squirro](https://squirro.com) | Enterprise GenAI | Centrale banken (ECB, Bank of England, Deutsche Bundesbank) |
| [Mindbreeze](https://www.mindbreeze.com) | Enterprise search + RAG | Oostenrijks; healthcare, finance, government |
| [Omnifact](https://omnifact.ai) | All-in-one AI platform | GDPR-first, banking, healthcare, public sector |

### Sector-specifiek

| Platform | Sector | Bijzonderheid |
|---|---|---|
| [Harvey](https://www.harvey.ai) | Legal | Puur legal, wachtlijst, >$100M valuation |
| [Corti](https://www.corti.ai) | Healthcare | "Europe's first sovereign healthcare AI infrastructure" |
| [EyeLevel / GroundX](https://www.eyelevel.ai) | Legal + enterprise | On-prem RAG, 97.83% accuracy (vs. LangChain 64%) |
| [Zep](https://www.getzep.com) | AI agent memory | SOC2, HIPAA BAA, GDPR right-to-be-forgotten via API |

---

## 2. Wie koopt dit en waarom

### Buyer profiel
- **Titel:** CTO, CDO, CISO — in gereguleerde sectoren ook compliance officer
- **Minimale bedrijfsgrootte:** 500+ medewerkers voor enterprise deals
- **Salescyclus:** 4–5 maanden enterprise, ~90 dagen mid-market

### Contractgrootte (Glean als benchmark)
| Segment | Jaarcontract |
|---|---|
| Mid-market (500–2.000 medewerkers) | $100K–$500K/jaar |
| Fortune 500 | >$5M/jaar |

### Wat ze écht voor betalen (prioriteitsvolgorde)

1. **Data sovereignty / residency** — 68% van financiële instellingen noemt dit als primaire blokkade voor adoptie. Wie dit niet kan leveren, zit niet eens aan tafel.
2. **Compliance & audit trails** — elke output herleidbaar naar brondocument. Voegt 20–30% toe aan prijs, niet onderhandelbaar in healthcare/finance/legal.
3. **Time to value** — verticale oplossingen scoren <1 maand TTv; horizontale platforms maanden.
4. **Permissions-aware retrieval** — bestaande toegangsrechten worden gerespecteerd in de RAG-laag.

---

## 3. Welke sectoren winnen

| Sector | Waarom sterk | Benchmark ROI |
|---|---|---|
| **Financiële dienstverlening** | Compliance-druk, enorme document volumes, audit verplichting | Squirro: €20M besparing in 3 jaar, ROI na 2 maanden |
| **Healthcare / Life Sciences** | HIPAA/GDPR, klinische data, drug discovery | Biopharma: $25M cost savings + $50–150M revenue uplift |
| **Legal** | Case research, contract review, compliance | Harvey: >$100M valuation puur op legal |
| **Government / Public sector** | Soevereiniteit, veiligheidsclearing | Vectara: US Air Force contract |
| **Insurance** | Claim processing, polisdocumentatie, PII-heavy | Sterk profiel, minder bediend |

**Patroon:** verticale specialisatie verslaat horizontale platforms. Pre-built knowledge voor een sector + compliance by default pakt 50%+ van de markt.

---

## 4. Hoe de concurrentie verkoopt

### Glean — horizontaal, breed
- Pitch: "Work AI for everyone"
- GTM: land via IT/ops, expand via use cases
- Kracht: 100+ connectoren, permissions-aware, snelle adoptie
- Zwakte: generiek — geen sector-specifieke kennis

### Vectara — developer-first API
- Pitch: "We trainen nooit op jouw data"
- GTM: zelfbediening voor instap, sales-led voor enterprise
- Kracht: privacy als kernpitch, HIPAA/SOC2, hallucination detection
- Zwakte: toolkit, geen turnkey product

### Sinequa — klassieke enterprise sales
- Pitch: data-volume pricing, grote referenties (Lufthansa, Freddie Mac, Telekom)
- GTM: directe sales bij Fortune 500
- Kracht: diep in life sciences (>50% leading life sciences companies)
- Zwakte: zwaar implementatietraject, verkocht aan ChapsVision

### Squirro — niche financial intelligence
- Pitch: "Trusted by central banks"
- GTM: ECB, Bank of England, Deutsche Bundesbank als referenties
- Kracht: extreem hoge geloofwaardigheid in smal segment
- Zwakte: beperkte schaalbaarheid buiten finance

### Harvey — vertical-only, legal
- Pitch: AI exclusief voor legal professionals
- GTM: wachtlijst, mond-tot-mondreclame, hoge prijzen
- Kracht: bewijs dat verticale focus extreme premium rechtvaardigt
- Les: één sector echt goed bedienen = verdedigbare moat

---

## 5. Marktcijfers

- **RAG markt 2024:** $1.2 miljard → verwacht $11 miljard in 2030
- **Enterprise RAG adoptie:** 51% (was 31% jaar eerder) — snelst groeiende AI-architectuur
- **Enterprise AI spend 2024:** $4.6 miljard (8x stijging vs. $600M in 2023)
- **RAG-as-a-Service markt:** $48.2M in 2024 → $185M in 2031 (CAGR 15.8%)
- **Governance premium:** 20–30% hogere prijs voor compliance/audit-ready deployments
- **Verticale TTv:** <1 maand voor sector-specifieke oplossingen vs. maanden voor generieke

---

## 6. Gaps in de markt — Klai's positie

Klai Knowledge stack: **AI + RAG + document intake (3rd party) + editable exposure layer (docs die terugvoeden in RAG)** + optioneel: sector-training per land.

| Gap in de markt | Klai's positie |
|---|---|
| Niemand is écht turnkey — allemaal toolkits met zware implementatietrajecten | Klai = "kant en klaar appliance" |
| Sector-specifieke pre-trained knowledge per land bestaat niet | Klai kan dit bouwen en standaard aanbieden |
| De feedback loop (docs → RAG → docs) ontbreekt bij de meeste | Klai heeft dit ingebouwd |
| EU-data-soevereiniteit is bottleneck #1 voor adoptie, maar weinig EU-native spelers | Klai is EU-native |
| Meeting notes → kennis → documentatie als één geïntegreerde flow bestaat niet | Klai combineert alle lagen |
| Pricing is onduidelijk bij nagenoeg alle spelers | Kans voor Klai om transparant te zijn |

---

## Bronnen

- [Glean $100M ARR](https://www.glean.com/press/glean-achieves-100m-arr-in-three-years-delivering-true-ai-roi-to-the-enterprise)
- [Glean Revenue — Sacra](https://sacra.com/c/glean/)
- [Vectara GTM Perspective](https://www.vectara.com/blog/vectara-vs-the-rest-a-go-to-market-perspective)
- [RAG as a Service Market Outlook 2025-2032](https://www.intelmarketresearch.com/rag-as-a-service-2025-2032-715-5161)
- [State of GenAI in the Enterprise 2024 — Menlo Ventures](https://menlovc.com/2024-the-state-of-generative-ai-in-the-enterprise/)
- [Squirro: State of RAG 2026](https://squirro.com/squirro-blog/state-of-rag-genai)
- [Enterprise RAG Sovereign AI](https://www.gaussalgo.com/knowledge-base/enterprise-rag-in-the-era-of-sovereign-ai-turning-data-into-business-value)
- [RAG Market Size — Grand View Research](https://www.grandviewresearch.com/industry-analysis/retrieval-augmented-generation-rag-market-report)
- [EyeLevel RAG Accuracy Benchmark](https://www.eyelevel.ai/post/most-accurate-rag)
- [Nuclia Privacy & Security](https://nuclia.com/privacy-security/)
- [OPAQUE Confidential RAG](https://www.opaque.co/confidential-agents-for-rag)
