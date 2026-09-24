# Chatkwaliteit: wat we onderzochten, wat we veranderden, en het plan

**Stand: 24 september 2026.** Samengesteld uit de SPEC-mappen en hun evolutielogboeken, de SPEC's die op 18 augustus uit de repo zijn verwijderd (te lezen met `git show 8f29b6fc9^:<pad>`), de git-geschiedenis van het chatpad, extern onderzoek en de metingen van 24 september. Hoe de chat **nu** werkt staat in [chat-system.md](chat-system.md); dit document gaat over hoe we daar kwamen, wat we zeker weten, en wat we willen verbeteren.

Elke conclusie staat naast de manier waarop ze gemeten is, omdat een conclusie zonder meetmethode de afgelopen weken meer dan eens langer bleef hangen dan ze verdiende.

**Afkortingen.** AJ = `docs/specs/SPEC-RAG-ANSWER-JUDGES-001/evolutie.md` (§2.x = meting x), AJ-spec = de `spec.md` in die map. IQ = `docs/specs/SPEC-RAG-INTAKE-QUALITY-001/evolutie.md`. CF, TIERS, EPI, CORR, SRC, RRR = de `spec.md` van SPEC-RAG-CLARIFY-FLOW-001, -ANSWER-TIERS-001, -ANSWER-EPISTEMICS-001, -CORRESPONDENCE-DISTILL-001, -SOURCE-SELECTION-001 en -RETRIEVAL-RUNTIME-RELIABILITY-001. Pad A = interne chat, pad B = widget. "Beoordelaar LLM" betekent een taalmodel als rechter, niet tegen menselijke oordelen geijkt tenzij vermeld.

---

## 0. Kort

**Wat we zeker weten**
1. Een opdracht in de prompt stuurt het antwoordmodel nauwelijks; een aparte stap of een controle in code wel. Dat kwam zes keer terug (§3, les 3).
2. Een aparte stap die vóór het schrijven uit de gevonden artikelen één vraag kiest, wint op de beurt ná die vraag, in twee rondes dezelfde kant op (AJ 2.47). Live op de widget sinds 24 september.
3. Bij alleen zwakke bronnen eerlijk "staat er niet" zeggen in plaats van een antwoord uit een naburig artikel te bouwen, wint duidelijk (34-17 en 40-14; AJ 2.48). Live op de widget sinds 24 september.
4. Twee herformuleringen van de eerste vraag, elk apart gezocht, winnen eind-tot-eind (63 om 43, twee rondes, beoordelaar mét de passages; AJ 2.33).
5. Controleren en repareren per zin haalt antwoorden met verzonnen details van 49% naar 11% (AJ 2.5); promptaanpassingen deden niets.
6. Ongeveer een derde van de antwoorden bevat een uitspraak die de artikelen niet dragen, en dat hangt niet af van hoe goed het zoeken was: 72/67/67% onderbouwd in de band high/medium/low (#1634).

**Wat zwakker is dan het klonk**
1. "Doorvragen maakt antwoorden slechter." Gemeten op de eerste beurt, met een model dat in één generatie moest antwoorden én vragen en dat bijna nooit deed (2 vervolgvragen op 150, AJ-spec 0.5.0). Het logboek erkent in 2.44 dat die meting weinig zegt over wat een goede vraag oplevert.
2. "Een rijkere eerste vraag wint 29 om 7." Negen vragen; de rijkere vraag was wat de bezoeker later zelf zei, en dat antwoord verzon vier keer zo vaak. Richting echt, omvang niet (AJ 2.25).
3. De RAGAS-winst van mei (+61% precisie, +154% recall): gemeten tegen een trefwoordenlijst, later "directional, not evidential" genoemd, nooit hermeten.
4. "De zekerheidsband zegt hoe betrouwbaar het antwoord is." Het is de hoogste score, met drempels uit twee scores van één incident; hij voorspelt niet of een beantwoord concept klopt.
5. "De interne chat repareert nu wat de artikelen niet dragen." Dat hij draait is bewezen; dat de interne antwoorden daardoor beter zijn, is nooit gemeten.

**De grootste open plek.** Alles wat sinds 17 september op de widget gemeten en verbeterd is (AJ 2.41 tot en met 2.48), zit niet in de interne chat. Die heeft ongeveer tien keer zoveel echte vragen, geen eigen meetset, en in de Open-modus geen enkele controle op verzonnen uitspraken.

---

## 1. Chronologisch: wat we onderzochten en veranderden

Kolommen: vraag · methode (n; echt of synthetisch; eerste beurt of heel gesprek; beoordelaar) · resultaat · codewijziging · stand nu · betrouwbaarheid · bron.

### Maart tot juli: zoekkwaliteit, weigeren bij lage zekerheid, taal

| Datum | Vraag | Methode | Resultaat | Code | Stand | Betrouwbaarheid | Bron |
|---|---|---|---|---|---|---|---|
| 30 mrt | Beter zoeken door weging op inhoudstype, leeftijd en volgorde? | Gepland 150 vragen RAGAS; opgeleverd met 5 placeholders | Nooit gemeten; 5 maanden schaduw: 24% van de volgordes veranderde, zonder koppeling aan kwaliteit | 4723739eb, 38dc732e8 | verwijderd 20 aug (#1122) | Geplande A/B nooit gedraaid | `8f29b6fc9^:.moai/specs/SPEC-EVIDENCE-001/spec.md` |
| 5 mei | Maakt contextual retrieval + parent-child + herschrijven + taxonomie het zoeken beter? | RAGAS, 30 handgeschreven vragen, één kennisbank, LLM-rechter | precisie 0,23 → 0,37, recall 0,25 → 0,64 | #329, #334, #338, #340 | live | Referentie bleek een trefwoordenlijst; herschrijven en taxonomie zitten in de hook die de eval overslaat; hermeting nooit bevestigd | `retrieval-improvements-roadmap.md:56-63`; `knowledge-rag-improvement-plan.md:233-240` |
| 6 mei | Antwoordt de chat in de taal van de vraag? | Deterministische check, 65 vragen, 6 talen | Doel ≥ 95%; het gemeten percentage staat nergens | #454 | live | Daarna vier reparaties na losse incidenten, nooit hermeten | `8f29b6fc9^:.moai/specs/SPEC-RAG-MULTILINGUAL-CHAT-001/spec.md` |
| 7-8 mei | Waarom verzon de chat integratieroutes; weigert hij na de fix? | Eén incident, 3 handgekozen vragen live | Top-score 0,18 → 0,99 op de incidentvraag; band zoals bedoeld | Zekerheidsband, top_k 20, anti-hallucinatietekst (#516-#518) | live; de band stuurt sindsdien weigeren en doorvragen op pad A | n=3; de geplande nametingen zijn nooit gerapporteerd | `docs/knowledge-retrieval-low-confidence-abstain-2026-05-08.md:134-149,202-208` |
| 6 jun | Letterlijke woorden overleven het herschrijven niet | – | – | Letterlijke vraag als eigen zoekleg (#797) | live | Geen meting | d96b790d9 |
| 11 jun | Zijn de RAGAS-cijfers van mei betrouwbaar? | Audit van de eval | Nee | Referentieantwoord verplicht | code live, hermeting open | – | `knowledge-rag-improvement-plan.md:233-240` |
| jul-20 aug | Kennisbank overslaan als die niet nodig is? | 30 dagen schaduw op echt verkeer | 3 op ~4.500 verzoeken | Verwijderd (#1122) | verwijderd | Echte data, eerlijk afgesloten | `knowledge-rag-improvement-plan.md:197-201` |

### Augustus: geplakte correspondentie en bronselectie (pad A)

| Datum | Vraag | Methode | Resultaat | Code | Stand | Betrouwbaarheid | Bron |
|---|---|---|---|---|---|---|---|
| 18 aug | Per deelvraag zoeken en bij risico naar een groter model | Geen meting | – | #1060 (fan-out, `klai-medium` bij risico) | live | Effect van de modelupgrade nooit gemeten | edaafcd6f |
| 17-18 aug | Waarom weigert de chat bij een geplakte klantmail terwijl het antwoord er is? | 3 zoekopdrachten, één incident | Hele mail als zoekvraag: niet in top-10; korte zoekvraag: plek 1, score 0,97 | Distillatie in de herschrijfaanroep; geplakte tekst als claims (#1059) | live | Diagnose sterk, één incident | CORR:29-43 |
| 18 aug | Maakt het model die schone zoekvraag betrouwbaar? | 2 live pogingen, daarna 3 synthetische canaries × 3 | Eerst mis; na promptcorrecties en regex-opschoning 9/9 top-5 | Code-opschoning | live | Alleen zoekniveau; de spec noemt het mechanisme "niet afdoende bewezen" | CORR:22-27 |
| 19 aug | Waarom nog een fout antwoord toen de distillatie werkte? | Eén productietrace | Goede chunks vielen weg op de bronnaam; band `high` door één chunk terwijl 6 van 7 ≤ 0,28 scoorden | Bronselectie gewijzigd (112457eba) | live | n=1; de geplande voor-en-na-meting is niet terug te vinden | SRC:56-80, 127-137 |
| 19-20 aug | Moet `high` twee sterke chunks vereisen? | Schaduwveld | Geen gedragseffect | Verwijderd (#1118, #1122) | verwijderd | Nooit tegen uitkomsten gemeten | SRC:370-377 |
| 19 aug | Het model nam de hypothese van de afzender over | Eén incident | Drie defecten in één alinea | Vier-sectiecontract, alleen meten | contract live, handhaving nooit gebouwd | De meting die handhaving moest onderbouwen is niet terug te vinden | EPI:51-61, 274-287 |
| 19 aug | Herschrijven valt terug bij 429 | Code en lokale harness | – | Herschrijven via de proxy met fallback (cbb3f57c1) | live | Nooit een foutpercentage vastgelegd; op 24 sep: 8 van 107 aanroepen mislukt sinds 10 sep | RRR:20-36 |

### September: de widget (pad B) en de interne reparatie

| Datum | Vraag | Methode | Resultaat | Code | Stand | Betrouwbaarheid | Bron |
|---|---|---|---|---|---|---|---|
| 11 sep | Nachtelijke LLM-beoordeling per gesprek | Gebouwd | Later: 172 webchatgesprekken, 35 opgelost, 66 vroeg afgebroken, `retrieval_miss` 25 | d76a4737a | widget aan, LibreChat uit | Menselijke steekproef en vergelijking met `klai-large` nooit uitgevoerd | SPEC-CHAT-QUALITY-LOOP-001; AJ-spec:72 |
| 15-17 sep | Rekenen widget en zoekdienst met dezelfde band? | Code lezen | Nee: dezelfde drempels 0,60/0,30, maar de widget rekende op de score vóór de boosts; sinds de fix neemt hij de band van de zoekdienst over | #1465 | live | Gelijkgetrokken; de drempels zelf zijn niet tegen uitkomsten getoetst | SPEC-KNOWLEDGE-ACTIVITY-001:134-146; eee4ecfa9 |
| 15 sep | Waarom weigert de widget "Can I also talk english?" | 540 echte widgetbeurten | 13,5% eindigde op de vaste weigering | Derde klasse "over dit gesprek" (#1443) | vervangen 17 sep | Echte beurten, één week | TIERS:50-55; PR #1443 |
| 17 sep | Nulmeting doorvragen op de widget | 80 synthetische vragen, 1 sample | 0 van 80 wedervragen; vage vragen 43% geweigerd | Doorvraagflow aan: widget #1474, intern #1475 | widget dezelfde middag vervangen (#1480); intern nog aan | Synthetisch; het effect op de widget is nooit gemeten (drie uur live); intern vuurde het in een maand 3 keer buiten testverkeer | CF:67-78; logs 24 sep |
| 17 sep | Welke antwoordvorm houdt een klein model vol? | `klai-fast`, 6 concepten × 3 rondes | Ja/nee "verzonnen?": altijd "nee"; keuzelijst 17/18 goed | Vraag- en antwoordbeoordelaar (#1480) | live | Synthetisch, klein | AJ 2.2 |
| 17 sep | Oud tegen nieuw, en helpt de doorvraagopdracht? | 50 echte eerste vragen × 3, blind, **zonder artikelen** | 69/65/16; waar de doorvraagopdracht meeging oud beter 19 van 23; 2 echte vervolgvragen op 150 | Opdracht verwijderd (#1486) | verwijderd | **Alleen beurt één**; model moest antwoorden én vragen; mat een antwoord met een aanhangsel | AJ 2.4; AJ-spec 0.5.0 |
| 17-18 sep | Wat helpt tegen verzonnen details? | 54 antwoorden zin voor zin | Lichte controle vangt 22%, zware 96% (precisie 77%); promptvarianten gelijk of slechter; controle + reparatie 49% → 11% | Controle per zin met reparatie (#1495) | live | n=54; wie nalas staat er niet bij | AJ 2.5 |
| 18 sep | Werkt het zoeken bij vervolgbeurten? | 90 echte vervolgbeurten, zoekniveau | Juiste artikel in top-8: 41%; letterlijke zoekregel 3 winst, 0 verlies | #1487, #1488 | live | Zoekniveau | AJ 2.6 |
| 18 sep | Helpt een rijkere eerste vraag? | 68 eerste vragen; 9 magere × 2, LLM paarsgewijs | 17/68 te mager; verrijkt wint 29 om 7; "≤ 6 woorden" 89% raak | Niets | – | n=9; de verrijking kwam van de bezoeker zelf; dat antwoord verzon 12/18 tegen 3/18; loste 6/18 op tegen 0/18 | AJ 2.7, 2.25 |
| 18 sep | Kort antwoord plus één vervolgvraag | 16 korte vragen × 2, blind | 15/12/5; verzonnen 4 → 9; 7 van 32 eindigden op een vraag | Niet samengevoegd | nooit live | n=16, beurt één, weer één generatie | AJ 2.10 |
| 18 sep | Heeft de interne chat hetzelfde probleem? | 80 interne en 80 widgetantwoorden, zware controle | Intern 85% ≥ 1 onbewezen uitspraak, widget 75% | Reparatie intern (#1526, #1530) | live | Dat reparatie intern betere antwoorden geeft is nooit blind gemeten | AJ 2.14, 2.22 |
| 18-19 sep | Hele gesprekken met een gesimuleerde bezoeker | 12 gesprekken, max 4 beurten | "Doel bereikt" 75% → na correcties van de scoorder 33% | #1519, #1541 | gereedschap | Scoorder was eerst hetzelfde model als de controle; n=12 | AJ 2.19, 2.24, 2.35 |
| 18 sep | Doorvragen op een taxonomie-aspect | 12 magere vragen | 7/12 → 7/12 | Niet gebouwd | afgevallen | n=12, zoekniveau | AJ 2.20 |
| 18-19 sep | Twee herformuleringen van de eerste vraag | 54 echte vragen; eind-tot-eind 2 rondes, `klai-large` **mét passages** | Zoekniveau 35% → 59%; eind-tot-eind 63 om 43; "lost op" 15 → 27; verzonnen 30 → 23; +2,3 s | #1548 | live; browser pas vanaf 22 sep (#1593) | De sterkste meting van het logboek; wel beurt één | AJ 2.27, 2.33 |
| 18 sep | Vorige antwoord als extra zoekleg bij vervolgbeurten | 70 vervolgbeurten | Zoekniveau 39% → 64%; eind-tot-eind 65/61, rondes tegengesteld; verzonnen 29 → 42 | Niet live | afgevallen | Zoekwinst is geen antwoordwinst | AJ 2.28, 2.32 |
| 18-19 sep | Waar ontbreekt het antwoord echt? | 54 kennisvragen | 6/54 niet in de kennisbank; 7/54 wel, maar niet vanuit de eerste vraag te vinden | Onderwerpen naar de redactie | niet opgepakt | LLM-oordeel over passages | AJ 2.29, 2.31, 2.36 |
| 19-22 sep | Intake: vraagvectoren, linktekst, vierde bron, prijsregels groeperen | 12-16 echte vragen × 3 × 2 | Telkens ruis of meer verzinsels | Niet uitgerold | afgevallen | Kleine n | IQ |
| 20 sep | Verdwenen decimale prijzen | 18 bedragen, deterministisch | 0/18 → 18/18 behouden | #1581 | live | Deterministisch, sterk | IQ |
| 22 sep | Wat zeggen menselijke oordelen over doorvragen? | 17 door de eigenaar beoordeelde gesprekken | 9 van 38 antwoorden bevatten een vraag; waar het systeem vroeg: goed of perfect | Meetset gebouwd | – | Eerste menselijke referentie | AJ 2.44 |
| 22 sep | Doorvragen als instructie in de prompt | 17 gesprekken; gesimuleerde bezoeker, `klai-large` beoordeelt de **volgende beurt**, 2 rondes | 20-10 en 16-16: ruis; vroeg niet vaker | Niet live | afgevallen | Eerste meting die beoordeelt wat de vraag oplevert | AJ 2.46 |
| 23-24 sep | Aparte stap kiest de ene vraag uit de gevonden artikelen | Idem, drie versies | Versie 3: 17-14 en 24-9; "begrijpt het probleem" 37 tegen 21, "juiste vervolgstap" 32 tegen 17 van 68; onnodige vraag 5/16 tegen 1/16 | `answer_plan` (#1620) | live | n=17, LLM-bezoeker en -rechter; nog niet op echt verkeer nagemeten | AJ 2.47 |
| 24 sep | Geen antwoord uit een zwakke bron | 27 echte beurten × 2; omslagen met de hand nagelezen | 34-17 en 40-14; "juiste vervolgstap" 74 tegen 30 van 108; "niet gevonden" 16 → 29 van 54 | #1635 | live | Rechter kreeg de nieuwe uitkomst als verwachting mee; daarom handmatig nagelezen, 1 echt verlies | AJ 2.48 |

### 24 september: deze sessie

| Vraag | Methode | Resultaat | Code | Stand |
|---|---|---|---|---|
| Herkent pad A twee vragen achter één vraagteken? | 18 achtergehouden berichten, deterministisch | 16 → 18 goed | Vraagtekens plus met en/and/or verbonden vragen (#1626) | live; effect op antwoorden niet gemeten |
| Meerdere vragen en moeilijkheid laten bepalen door de herschrijfaanroep | 292 live `klai-fast`-aanroepen, 73 berichten | Meerdere vragen 36/42 tegen regel 40/42, +250 ms; moeilijkheid 25/31 tegen tokenregel 15/31 | Niet gebouwd | afgevallen; geen bewijs dat `klai-large` op moeilijke vragen beter antwoordt |
| LiteLLM's ingebouwde complexity router | 19 Nederlandse eval-vragen en hun Engelse vertaling | Alles `SIMPLE` | Niet gebouwd | afgevallen |
| Encodermodel Laya zero-shot | 19 routeringsvragen, 24 berichten | Onder de meerderheidsbasis; gelijk aan de oude vraagtekenregel | Niet gebouwd | afgevallen |
| Voorspelt de zekerheidsband de kwaliteit? | 254 preview-beurten op één helpdeskwidget | Onderbouwd 72/67/67%; weigering of buiten onderwerp 60% bij low, 9% bij high | Kalibratiescript, drempels gehouden (#1634) | live als gereedschap |
| Hoe vaak draaien verduidelijken en herschrijven op pad A? | Productielogs, 30 dagen | Verduidelijken 3 keer buiten testverkeer (323 keer bij één testaccount); herschrijven 8 van 107 keer mislukt | – | – |

---

## 2. Per thema

**Vervolgvragen.** *Zeker:* een opdracht aan het antwoordmodel werkt niet (2 op 150, 7 op 32, 4 op 12); een aparte stap die de vraag uit de gevonden artikelen kiest wel (AJ 2.47). *Zwak:* "doorvragen schaadt" rust op eerste-beurtmetingen van die opdracht. *Nooit getest:* de vraagstap op echt verkeer; verduidelijken op pad A (ging in een maand 3 keer af); echte in plaats van gesimuleerde bezoekers.

**Zekerheidsband en weigeren.** *Zeker:* de band is de hoogste score na herrangschikken; hij scheidt de kwaliteit van beantwoorde concepten niet (72/67/67%) maar wel weigeringen en buiten-onderwerp (60% tegen 9%). De regel "alle bronnen < 0,4, zeg eerlijk dat het er niet staat" wint op echte beurten (AJ 2.48). *Zwak:* de drempels 0,60/0,30 zelf. *Nooit getest:* nieuwe grenzen op score (de widget slaat de beslissende score nog niet op); de band als poort op pad A tegen antwoordkwaliteit.

**Meerdere vragen.** *Zeker:* de nieuwe regel haalt 18/18; de herschrijfaanroep doet het slechter. *Nooit getest:* het effect op antwoorden; de widget kent geen splitsing per vraag.

**Zoeken en de eerste vraag.** *Zeker:* twee herformuleringen winnen (63-43); zoekwinst is geen antwoordwinst (drie keer). *Zwak:* de RAGAS-winst van mei. *Nooit getest:* herschrijven en taxonomie op pad A eind-tot-eind; herformuleringen op pad A.

**Verzonnen details.** *Zeker:* controle per zin plus reparatie werkt (49% → 11%), prompts niet; repareren bij één melding maakt het slechter. *Zwak:* dat de reparatie intern even goed werkt. *Nooit getest:* de reparatie op pad A tegen een menselijke referentie; controle in Open.

**Modelkeuze.** *Zeker:* de ingebouwde router en Laya zijn onbruikbaar voor Nederlandse supportvragen. *Nooit getest:* of `klai-medium`/`klai-large` betere antwoorden geven op dezelfde vragen. De upgrade naar `klai-medium` draait sinds augustus zonder meting, en de routerbeslissing staat niet in de logs.

**Taal.** *Zeker:* de taal wordt per beurt uit het hele gesprek afgeleid en wisselt alleen op een duidelijk signaal. *Zwak:* dat het betrouwbaar klopt; een percentage is nooit vastgelegd. *Nooit getest:* antwoordkwaliteit in het Engels.

**Widget tegenover interne chat.** *Zeker:* beide hebben het probleem van onbewezen uitspraken, intern iets meer (85% tegen 75%). Alles van AJ 2.41-2.48 zit alleen in de widget (AJ 2.49). *Nooit getest:* elke verbetering van de widget op pad A.

---

## 3. Lessen over meten die steeds terugkomen

1. **Alleen de eerste beurt gemeten, conclusie over gesprekken getrokken.** Een vraag is pas iets waard in de beurt erna. Pas AJ 2.46 en 2.47 beoordelen de volgende beurt.
2. **De LLM-rechter verkiest het volledige antwoord**, ook als het verzint (29 om 7 terwijl het vier keer zo vaak verzon; zonder artikelen ziet hij verzinsels niet). Hij kiest iets vaker het eerst getoonde antwoord (76 tegen 58). Geen rechter is tegen een menselijke set geijkt; die set bestaat sinds 22 september (17 gesprekken).
3. **Een opdracht aan het antwoordmodel verandert zelden zijn gedrag.** Doorvragen, strenger tegen verzinsels, commerciële vragen weren, de hypothese van de afzender vermijden, distilleren: steeds weinig effect. Een aparte stap of code-controle werkte wel.
4. **Kleine n en één ronde.** Het logboek noemt minder dan ongeveer tien beurten verschil ruis en eist twee rondes; veel conclusies staan op minder. Voor een voorkeur van 65/35 zijn ongeveer 85 niet-gelijke paren nodig (§4.3).
5. **Zoekwinst is geen antwoordwinst.** Drie keer meer gevonden zonder beter antwoord.
6. **De meting nam een andere route dan de gebruiker.** De herformuleringen waren drie dagen "live" zonder dat de browser ze kreeg; de interne eval gaat buiten de LiteLLM-hook om.
7. **Het meetinstrument beloonde zichzelf.** Scoorder en controle op hetzelfde model; een doorverwijzing telde als succes.
8. **Drempels zonder herkomst.** 0,60/0,30 uit twee scores van één incident.
9. **Schaduwexperimenten zonder uitkomstmeting.** Evidence tier, de "corroborated band" en de retrieval gate: maanden gelogd, nooit aan antwoordkwaliteit gekoppeld.
10. **Kleine synthetische sets waar echte data was.** De interne chat heeft tien keer zoveel echte vragen en is nooit als meetset gebruikt.

---

## 4. Wat extern onderzoek zegt

Onderzoek van 24 september, bronnen 2024-2026. Dit bevestigt de lijn die het logboek sinds 22 september volgt.

### 4.1 Wanneer antwoorden, wanneer vragen, wanneer eerlijk "niet gevonden"
- **De beste voorspeller is niet de zoekscore, maar de vraag "staat het antwoord in wat we gevonden hebben?"** Een aparte check daarop, gecombineerd in een simpel model, verhoogde het aandeel juiste antwoorden onder de beantwoorde vragen met 2 tot 10 procentpunt ([Sufficient Context, Joren e.a.](https://arxiv.org/abs/2411.06037)). Dat past bij de 72/67/67% van Klai.
- **Of extra werk nodig is, zie je pas ná het zoeken.** In een productiesysteem van 20.000 vragen konden classificatoren dat vooraf niet voorspellen ([Coverage Illusion](https://arxiv.org/abs/2605.27220)).
- **Een vraag helpt alleen als de kennis er is maar de vraag onduidelijk.** Staat het antwoord niet in de kennisbank, dan lost doorvragen niets op ([Clarify When Necessary, NAACL 2025](https://aclanthology.org/2025.findings-naacl.306/)). Dan hoort er een eerlijk "staat er niet" met een route naar een mens, zoals Intercom Fin doet ([Intercom](https://www.intercom.com/help/en/articles/11813803-understanding-when-fin-ai-agent-may-not-provide-an-answer)).
- **Modellen vragen uit zichzelf bijna nooit, en opgehaalde context maakt het nog zeldzamer** ([Knowing but Not Showing](https://arxiv.org/abs/2605.25284); [CLAMBER, ACL 2024](https://aclanthology.org/2024.acl-long.578/)). Een aparte beslisstap op zoeksignalen wint van een gepromptte beslissing ([ASK, ACL 2025 Industry](https://aclanthology.org/2025.acl-industry.63.pdf)).
- **Een vraag moet specifiek zijn en uit de artikelen komen**; slechte vragen verlagen de tevredenheid ([Zou e.a. 2023](https://www.sciencedirect.com/science/article/abs/pii/S0306457322002771); [Siro e.a., EACL 2024](https://aclanthology.org/2024.findings-eacl.84/)).
- **Na het antwoord op een vraag: samenvoegen en opnieuw zoeken.** Over meerdere beurten verspreide informatie gaf gemiddeld 39% slechtere prestaties dan één volledige vraag ([Laban e.a. 2025](https://arxiv.org/abs/2505.06120)); de winst van ASK komt uit opnieuw zoeken met het antwoord van de gebruiker.
- **Zekerheid uit het antwoordmodel zelf** (entropie, meerdere steekproeven) werkt slecht in RAG ([Soudani e.a., ACL Findings 2025](https://aclanthology.org/2025.findings-acl.852/)).

### 4.2 Minder verzonnen uitspraken
- **Letterlijker overnemen en eerst de bronzinnen kiezen** vermindert verzinsels ([CopyPasteLLM](https://arxiv.org/abs/2510.00508); [Attribute First, then Generate, ACL 2024](https://arxiv.org/abs/2403.17104)). Promptinstructies helpen weinig ([Trust-Align, ICLR 2025](https://arxiv.org/abs/2409.11242)).
- **Meer context is niet beter**: relevant ogende maar foute passages trekken het model mee ([Long-Context LLMs Meet RAG](https://arxiv.org/abs/2410.05983)).
- **Langere antwoorden gaan samen met meer verzinsels** (samenhang op de [Vectara-leaderboard](https://github.com/vectara/hallucination-leaderboard)); bij Klai stegen verzinsels van 4 naar 9 toen antwoorden langer moesten.
- **Controle per uitspraak is de standaard** ([FActScore](https://arxiv.org/abs/2305.14251), [MiniCheck](https://arxiv.org/abs/2404.10774)), wat Klai al doet.

### 4.3 Meten
- **Een oordeel over één beurt bevoordeelt het volledige antwoord boven een goede vraag** ([Zhang, Knox en Choi, ICLR 2025](https://arxiv.org/abs/2410.13788)).
- **Gesimuleerde bezoekers zijn bruikbaar maar te coöperatief**; uitkomsten verschillen tot 9 punt per simulatormodel, en met ongeduldige of kortaffe bezoekers zakken systemen fors ([Lost in Simulation](https://arxiv.org/abs/2601.17087); [Non-Collaborative User Simulators](https://arxiv.org/abs/2509.23124)).
- **LLM-rechters hebben bekende vertekeningen** (volgorde, voorkeur voor de eigen modelfamilie) ([CALM](https://llm-judge-bias.github.io/); [Panickssery e.a., NeurIPS 2024](https://arxiv.org/abs/2404.13076)). Beoordelen in beide volgordes en ijken tegen een menselijke set is de gangbare remedie.
- **Steekproef**: voor een voorkeur van 70/30 zijn ongeveer 47 niet-gelijke paren nodig, voor 65/35 ongeveer 85, voor 60/40 ongeveer 194 (tekentoets, α 0,05, power 80%).

### 4.4 Modelkeuze
- **Groter is niet vanzelf trouwer aan de bron** ([FaithEval, ICLR 2025](https://arxiv.org/abs/2410.03727)). Op de Vectara-leaderboard (samenvatten, geen vraag-antwoord) verzint `mistral-3-large-2512`, ons `klai-large`, in 14,5% van de gevallen, tegen 4,5% voor de oudere `mistral-large-2411`. Modelkeuze moet dus op onze eigen vragen met vaste passages gemeten worden.

---

## 5. Bevindingen waar nog niets mee gedaan is

| Bevinding | Omvang | Bron |
|---|---|---|
| Een derde van de antwoorden bevat een onbewezen uitspraak | 47 van 150; zin voor zin 49% vóór reparatie | AJ 2.4, 2.5 |
| Een kwart van de eerste vragen is te mager | 17 van 68 | AJ 2.7 |
| Onderwerpen ontbreken aantoonbaar in de kennisbank | 6 van 54 kennisvragen; 7 alleen met later gegeven details te vinden | AJ 2.31, 2.36 |
| De late grounding-controle logt wat hij vond, maar niemand leest het | 9 van 54 eerste beurten afgekapt bij 4 s | AJ 2.37, 2.39 |
| De meetpunten onder Platform → Status hebben geen lezer | acht meetpunten, geen alarm of vaste controle | AJ 2.40 |
| Herschrijven mislukt op pad A | 8 van 107 aanroepen sinds 10 sep | logs 24 sep |
| De nachtelijke beoordeling noemt `retrieval_miss` de grootste faalcategorie | 25 van 172 gesprekken; 66 vroeg afgebroken | AJ-spec:72 |
| De RAGAS-hermeting na de referentiefix | sinds 11 juni open | `knowledge-rag-improvement-plan.md:233-240` |
| De vraagbeoordelaar noemt één op zeven widgetbeurten onduidelijk, maar dat bepaalt alleen nog of een bronloze wedervraag zonder knoppen wordt getoond | ~1 op 7 | AJ 2.44 |

---

## 6. Correcties aan de documentatie in deze wijziging

Gevonden door de code naast de documenten te leggen (volledige lijst met ankers in [chat-system.md](chat-system.md) en hieronder):

| Document | Stond er | Klopt nu | Gedaan |
|---|---|---|---|
| SPEC-RAG-CLARIFY-FLOW-001 kop | Doorvraagflow live op de widget, interne chat "volgende" | Omgekeerd: intern draait hij, de widget gebruikt sinds #1480 de beoordelaars en sinds #1620 het antwoordplan | kop gecorrigeerd |
| `knowledge-rag-improvement-plan.md` B2 | `klai-medium` is nooit een routeringsdoel | De router kiest `klai-medium` sinds 18 aug; de ingebouwde complexity router is op 24 sep gemeten en afgewezen | gecorrigeerd |
| SPEC-RAG-ANSWER-JUDGES-001 spec | Open-antwoorden worden "alleen gemeten"; doorvragen "gemeten schadelijk" | Open wordt niet gemeten; alleen de opdracht aan het antwoordmodel was schadelijk, een aparte vraagstap wint (2.47) | gecorrigeerd |
| AJ §5 | Doorvragen vóór het antwoord en keuzes uit artikelen: "niet meer proberen" | Achterhaald door 2.47 | gecorrigeerd |
| SPEC-KNOWLEDGE-ACTIVITY-001 | De widget leest de band niet en bewaart hem niet | Hij wordt bewaard in `answer_signals`, en stuurt nog steeds niets | gecorrigeerd |
| `knowledge-retrieval-flow.md` | Zeven uitspraken over het chatpad kloppen niet meer (widget via de hook, top_k 5, guardrails via LLM, timeout 3 s, e.a.) | Zie chat-system.md | waarschuwing bovenaan met de lijst |
| `regular-chat-knowledge-retrieval-citations.md` | Strict vervangt tekst zonder bron altijd door een weigering; regelnummers voorbij het einde van het bestand | De claims-check laat tekst zonder beweringen door | waarschuwing bovenaan |
| Codecommentaar (`deploy/litellm/config.yaml`, `docker-compose.yml`, docstring `klai_knowledge.py`) | PII-handhaving "INERT, default OFF"; hook "skips silently" | Standaard aan; hook weigert | **niet** in deze wijziging (zou een deploy starten), zie plan 4.2 |

---

## 7. Plan (ter goedkeuring)

### 7.1 Uitgangspunten

1. **Meer context over de vraag geeft een beter antwoord.** De richting is gemeten (AJ 2.25) en past bij het onderzoek; de winst komt uit de goede vraag op het goede moment, niet uit een instructie.
2. **Weet het systeem het niet, dan geen slecht antwoord.** Twee gevallen die verschillend behandeld moeten worden: de kennis is er maar de vraag is onduidelijk, dan één gerichte vraag; de kennis is er niet, dan eerlijk zeggen dat het er niet staat, met een route naar een mens. Doorvragen lost het tweede niet op.
3. **Die beslissing valt in een eigen stap na het zoeken**, op basis van wat er gevonden is, niet als instructie aan het antwoordmodel en niet op de zekerheidsband.
4. **Bouwen en vergelijken met het oude, niet schaduwdraaien.** Elke wijziging wordt herspeeld op echte historische vragen tegen de huidige code, beoordeeld op de beurt ná een eventuele vraag, in twee rondes. Wijst het beide keren dezelfde kant op, dan gaat het live voor iedereen, en daarna volgen we het productiesignaal.
5. **Hergebruik wat bewezen is.** Wat op de widget gewonnen heeft, gaat eerst naar de interne chat, via gedeelde code.

### 7.2 Fase 0: fundament (ongeveer 3 dagen)

Zonder deze stap is elke volgende meting weer te klein of op de verkeerde route.

- **0.1 Het herspeelgereedschap in de repo.** De metingen van AJ 2.46-2.48 draaien met gereedschap buiten de repo. Dat wordt een operatorscript in `klai-portal/backend/scripts/` voor beide paden: historische widgetgesprekken én LibreChat-vragen (tien keer zoveel) opnieuw door de huidige code, met het zoeken zoals het nu scoort. Een gesimuleerde bezoeker die alleen weet wat de echte bezoeker later zei, in drie persona's (coöperatief, kortaf, ongeduldig). Het oordeel gaat over de beurt ná een vraag, blind in beide volgordes, in twee rondes. Beslissen vanaf ongeveer 85 niet-gelijke paren; kleinere sets alleen om te zien of een ingreep aankomt.
- **0.2 Een menselijke ijkset van ongeveer 50 gesprekken** (nu 17), zodat we weten hoe vaak de LLM-rechter het met een inhoudseigenaar eens is.
- **0.3 Zichtbaar maken wat pad A beslist.** De routerbeslissing op warning-niveau (één regel). Per beurt één beslisregel met band, score, verduidelijkt ja/nee, model, meervoudige vraag en grounding-uitkomst, zodat de interne chat dezelfde meetbaarheid krijgt als `answer_signals` op de widget. Op de widget de score opslaan waarop de band beslist, naast `top_score`.
- **0.4 De kaart actueel houden.** Een test die faalt als er in het chatpad een LLM-aanroep bijkomt of verdwijnt zonder dat [chat-system.md](chat-system.md) meeverandert, en een regel die de kaart laat laden zodra iemand de chatcode aanraakt.

**Klaar als:** het script draait op beide paden en reproduceert de uitkomst van AJ 2.48 op dezelfde beurten; de routerbeslissing en de beslisregel staan in de logs.

### 7.3 Fase 1: de interne chat op het niveau van de widget (ongeveer een week)

Dit is de grootste bekende winst: vier verbeteringen die op de widget gewonnen hebben, ontbreken intern.

- **1.1 Het antwoordplan (`answer_plan`) ook op pad A**, in plaats van de verduidelijkingsinstructie bij een lage band. Gedeelde code via `klai-libs`.
- **1.2 De zwakke-bronnenregel ook op pad A.** In Strict: eerlijk zeggen dat het niet in de kennisbank staat; in Open: dat zeggen en dan duidelijk gemarkeerd uit algemene kennis antwoorden.
- **1.3 De twee herformuleringen van de eerste vraag ook op pad A**, naast de bestaande herschrijfaanroep.
- **1.4 Grounding-controle in Open ten minste meten** (nu niets); daarna op basis van de cijfers beslissen of de reparatie ook daar hoort.

Elk onderdeel apart herspelen op LibreChat-vragen tegen de huidige code; live alleen bij twee rondes dezelfde kant op. **Klaar als:** 1.1-1.3 winnen of zijn met cijfers afgewezen, en 1.4 levert een percentage.

### 7.4 Fase 2: één beslisstap na het zoeken, op beide paden (ongeveer een week)

Jouw principe volledig uitgewerkt, voortbouwend op het antwoordplan.

- **2.1 Het antwoordplan krijgt een vierde vraag mee: "staat het antwoord in deze passages?"**, met als uitkomst volledig, gedeeltelijk of niet. Dat hoort in dezelfde stap, maar niet zonder kosten: het antwoordplan ziet nu hooguit 8 passages van 400 tekens binnen 2 s, en dit oordeel heeft de volledige passages nodig, dus invoer en tijdsbudget veranderen en worden meegemeten. Het vervangt de vaste grens "alle bronnen < 0,4" en de band als trigger; de band blijft als meetpunt. De beslistabel:
  - *volledig*: antwoorden, ook als de vraag kort is;
  - *gedeeltelijk*: beantwoorden wat er staat en precies één gerichte vraag naar wat ontbreekt;
  - *dubbelzinnig* (bestaande `diagnose`/`choose`): één vraag met opties uit de artikelen;
  - *niet*: eerlijk "staat er niet", met de afspraakknop op de widget en intern met wat de kennisbank wél dekt.
  Eerst ijken tegen ongeveer 100 door een mens gelabelde vraag-plus-passages (precisie en recall per uitkomst), dan herspelen.
- **2.2 Na het antwoord op een verduidelijkingsvraag: samenvoegen en opnieuw zoeken.** Vraag plus antwoord worden één zelfstandige zoekvraag. Eerst meten wat de bestaande herschrijving en coreferentie daar nu al van maken.
- **2.3 Magere eerste vragen** (een kwart) herkennen met de regel "zes woorden of minder" plus de spreiding van de gevonden artikelen, niet met een LLM-oordeel over vaagheid.

**Klaar als:** de beslisstap wint op het herspelen van beide paden, en op echt verkeer daarna het aandeel "juiste vervolgstap" niet daalt en het aandeel onbewezen uitspraken niet stijgt.

### 7.5 Fase 3: minder verzonnen uitspraken (ongeveer een week)

- **3.1 Kortere widgetantwoorden: eerst 1 tot 3 bronzinnen kiezen, dan alleen daaruit schrijven**, voor procedure- en prijsvragen. Meten per uitspraak én per antwoord, en of het doelfeit er nog in staat, zodat korter niet alleen wint door minder te zeggen.
- **3.2 Modelkeuze meten op vaste passages.** Small, medium en large op dezelfde ongeveer 150 vragen met exact dezelfde passages. Tot die uitslag verandert de router niet, en komt er geen routering op moeilijkheid.
- **3.3 "De reparatie haalde het meeste weg"** wordt een signaal voor een deelantwoord of een mens.

### 7.6 Fase 4: klein onderhoud (ongeveer een dag, kan parallel)

- **4.1** Uitzoeken waarom 8 van 107 herschrijfaanroepen op pad A mislukken (timeout 1,5 s bij een p95 van 1,2 s).
- **4.2** Misleidend codecommentaar corrigeren (PII "INERT", docstring "skips silently").

### 7.7 Wat we niet doen

- Het antwoordmodel in dezelfde generatie laten doorvragen.
- Een LLM alleen laten beslissen of een vraag te vaag is (45% vals "onduidelijk" gemeten).
- De zekerheidsband als weigertrigger gebruiken.
- Doorvragen beoordelen op de eerste beurt, of beslissen op 12 tot 30 paren.
- Meer passages of bronnen toevoegen om gaten te dichten.
- Aannemen dat het grotere model trouwer is.
- Zekerheid uit het antwoordmodel zelf afleiden.
- Altijd doorvragen bij korte vragen: een korte vraag waarvoor één artikel duidelijk wint, krijgt gewoon een antwoord.

### 7.8 Waar het plan na goedkeuring leeft

Als fasen in SPEC-RAG-ANSWER-JUDGES-001, dat de metingen van deze keten al bijhoudt; de uitkomsten per stap gaan in het bijbehorende logboek. Geen nieuwe SPEC.
