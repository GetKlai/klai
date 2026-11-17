---
id: SPEC-CHAT-INTERNAL-ROUTE-001
version: "0.1.0"
status: design, stap 1 in bouw; nog niets uitgerold
created: 2026-11-17
updated: 2026-11-17
priority: high
tenant_scope: platform-wide voor de interne chat; de helpwidget en de partnersleutel veranderen niet van gedrag
related:
  - docs/architecture/chat-system.md (hoe één beurt nu loopt)
  - docs/architecture/chat-quality-history-and-plan.md (§5, §5a, §6 werkafspraken, §7.4 het besluit van 24 september voor één pad)
  - docs/runbooks/meetlat.md
---

# De interne chat krijgt een eigen regie op de gedeelde kennislaag

Elke bewering draagt een label: [gemeten], [onderzoek], [praktijk] of [aanname], zoals de werkafspraken vragen.

## 1. Aanleiding

Op 17 november 2026 schreef de interne chat op een vraag van vijf woorden een antwoord van ruim twaalfduizend tekens, waarin dezelfde zinnen tot acht keer terugkwamen. In de interne chat van één organisatie viel in veertien dagen 7 van de 16 antwoorden in herhaling; van alle interne antwoorden in vier weken was 18% langer dan drieduizend tekens, tegen geen enkel antwoord op de helpwidget [gemeten].

De oorzaak is nagebootst op het productiemodel, 58 losse aanroepen [gemeten]:

| Wat de schrijfstap meekreeg | Runs | Ontspoord |
|---|---|---|
| Gereedschapsdefinities en het vaste antwoordformaat samen, zoals productie | 8 | 8 |
| Geen gereedschappen, formaat behouden | 6 | 0 |
| Gereedschappen, geen formaat | 6 | 0 |
| Als productie, temperatuur 0,15 | 6 | 6 |
| Als productie, zonder de opdracht "stappen per variant" | 6 | 6 |
| Als productie, grootste passage ingekort | 6 | 6 |

De interne voorkant hangt aan elk verzoek de definities van de tien gereedschappen van de kennisserver. De portal geeft die ongewijzigd door aan de aanroep die het antwoord schrijft, en die aanroep draagt ook het vaste formaat dat de antwoordtaal afdwingt. Met beide samen laat het model het formaat los, schrijft vrije tekst die niet ophoudt, en vraagt in 25 van 40 runs een gereedschap aan waar niemand om vroeg (opslaan in de persoonlijke of de organisatiekennis, een compliancecheck). Een grens op de lengte of een stop bij herhaling bewaart in alle gemeten gevallen het goede begin van het antwoord, maar laat drie- tot vierduizend tekens doorlopende tekst door: dat is een zekering en geen oplossing [gemeten].

De interne meetlat stuurt de gereedschappen en het voorvoegsel van de voorkant niet mee, en kon deze fout dus niet zien [gemeten].

## 2. Wat het onderzoek zegt

**Waarom er één pad is.** Tot 24 september liepen er twee paden, en "die twee paden waren de plek waar verbeteringen en kennis steeds uit elkaar liepen". Na het samenvoegen daalde verzonnen inhoud in de interne chat van 13 naar 2 op 42 antwoorden [gemeten, 25 september]. Een eigen ontwerp voor de interne chat is nooit overwogen; het besluit was gericht tegen twee paden die elkaar bijhouden, en liet "een tweede pad alleen waar gebruikers of modi het echt vragen" open. Het ene pad telt nu ongeveer 67 vertakkingen op oppervlak, modus of intern [gemeten].

**Hoe de interne chat gebruikt wordt** [gemeten, vier weken, alle 36 gesprekken van één organisatie gelezen en alleen geteld]:

| Soort werk | Aandeel | Kenmerk |
|---|---|---|
| Korte productvraag, zoals een klant die stelt | 53% | mediaan 11 woorden, niets geplakt |
| Geplakte klantmail of ticket, om een antwoord op te maken of uit te zoeken | 22% | mediaan 200 woorden |
| Geplakt technisch verslag om te analyseren | 6% | mediaan 642 woorden |
| Overig (feiten over het bedrijf, vragen over de chat zelf, een verzoek) | 19% | |

Op de helpwidget is geen enkel bericht langer dan 47 woorden en wordt nooit iets geplakt. De gereedschappen zijn in vier weken negen keer aangevraagd, door twee organisaties; er waren geen agents, geen websearch en vrijwel geen bijlagen [gemeten].

**Hoe anderen het doen.** Zes leveranciers met zowel een klantgerichte agent als een hulp voor medewerkers delen de kennislaag en geven elke doelgroep een eigen regie [onderzoek, drie in de bron gelezen, drie uit zoekfragmenten]. Voor gereedschappen samen met een vast antwoordformaat documenteren twee modelleveranciers dat het samen kan; de leverancier van ons model zegt er niets over; de gangbare omweg is twee stappen, eerst de gereedschappen en het formaat alleen op het eindantwoord [onderzoek]. Een vast pad past bij een goed omschreven taak, en een vrij pad waarin het model zelf zijn stappen kiest alleen waar die vrijheid nodig is [onderzoek]. Bewijs dat een klein model die regie betrouwbaar voert, is niet gevonden.

## 3. Ontwerp

De kennislaag blijft gedeeld: de zoekdienst, de keuzestap, de controle op onderbouwing, de bronvermelding en de meetlat. De helpwidget en de partnersleutel houden hun pad en hun gedrag. De interne chat krijgt een eigen regie die eerst vaststelt wat voor werk een beurt is, en die alleen de gedeelde stappen aanroept die bij dat werk horen. Dit is geen tweede pad dat het eerste bijhoudt: er komt geen tweede zoekvraag, geen tweede keuzestap en geen tweede controle.

| Soort werk | Wat de regie doet |
|---|---|
| Productvraag | Zoals nu: zoeken, passages kiezen, alleen daaruit schrijven; in Strict een eerlijke weigering, in Open een label bij algemene kennis. |
| Werken met geplakte tekst | Zoekvragen uit het geplakte materiaal, passages kiezen, en een concept of analyse schrijven waarin zichtbaar is wat uit de artikelen komt. Geen weigering: de medewerker beslist wat hij ermee doet. |
| Een handeling | Een aparte stap met de gereedschappen en zonder het vaste formaat, alleen als de medewerker om de handeling vraagt of een lopende gereedschapsronde doorgaat. |
| Overig | Zoals nu (algemene modus, een vraag over het gesprek zelf). |

Elke schrijfaanroep, op elk oppervlak, krijgt een grens op de lengte en een stop bij herhaling als zekering.

De interne meetlat stuurt mee wat de voorkant in productie meestuurt, zodat een ronde ziet wat een medewerker krijgt.

## 4. Stap 1: de fout eruit, de zekering erin, de meting ziend

Contract:

1. De schrijfaanroep draagt alleen gereedschappen op een handelingsbeurt. Een beurt is een handelingsbeurt als het verzoek gereedschappen meebrengt en ofwel het laatste bericht van het gesprek een gereedschapsresultaat is, ofwel de lezing van de zaak een handelingswens meldt met een citaat dat letterlijk in het laatste bericht van de medewerker staat. Dat is hetzelfde patroon als de contactwens op de widget, waar code het citaat natrekt. Bij twijfel is het geen handelingsbeurt.
2. Op een handelingsbeurt draagt de schrijfaanroep geen vast antwoordformaat. Op elke andere beurt draagt ze geen gereedschappen en geen gereedschapskeuze.
3. Elke schrijfaanroep stuurt een grens op de lengte mee, instelbaar, standaard 2.000 tokens. Van 1.725 schrijfaanroepen in veertien dagen onder de 1.500 tokens was de 99e percentiel 618 tokens [gemeten]; een geldig antwoord wordt dus niet afgekapt.
4. Komt in een antwoord een zin van meer dan veertig tekens voor de derde keer voor, dan eindigt het antwoord vóór die derde keer, ook als het gestreamd wordt. Een antwoord met twee varianten die dezelfde stappen hebben, wordt niet afgekapt.
5. Het record van de beurt legt vast of er gereedschappen zijn aangeboden, of het een handelingsbeurt was, en of het antwoord is afgekapt en waarom.
6. De interne meetlat kan de gereedschapsdefinities en het voorvoegsel van de voorkant meesturen, uit een bestand dat de aanroeper opgeeft.
7. De helpwidget en de partnersleutel veranderen niet, behalve dat punt 3 en 4 ook voor hun schrijfaanroep gelden.

De waarde 2.000 en de regel "derde keer" zijn bewuste vereenvoudigingen. De bovengrens is een antwoord dat terecht langer is dan ongeveer zesduizend tekens; komt dat voor, dan wordt de grens per soort werk instelbaar.

Meting: aandeel ontspoorde antwoorden op het nagebouwde productieverzoek, productiemodel · nu 8 van 8 (17 november 2026) · verwacht 0 van 8 · bewezen na de bouw met acht herhalingen; terug als er één ontspoort. Daarnaast twee rondes van de interne meetlat op de hoofdversie en twee op de kandidaat op dezelfde dag, en op echt verkeer het aandeel interne antwoorden boven drieduizend tekens: nu 18%, verwacht onder 3% in de twee weken na uitrol.

## 5. Stap 2: werken met geplakte tekst

Voorwaarde vooraf: het kleine model dat de zaak leest, herkent de soort werk betrouwbaar. Dat wordt getoetst op echte eerste berichten voordat er iets gebouwd wordt [aanname tot de toets er is].

Contract in hoofdlijnen, definitief na die toets: de lezing van de zaak krijgt een veld voor de soort werk. Bij geplakt materiaal komen de zoekvragen uit het materiaal, kiest de keuzestap passages bij de vragen die het materiaal stelt, en schrijft de schrijver een concept of analyse met de bronnen erbij. De controle op onderbouwing meet, zoals nu, en verandert het antwoord niet. In Strict blijft gelden dat een bewering over het product uit de artikelen komt; wat het materiaal zelf zegt, mag de schrijver gebruiken.

Meting: per soort werk echte gevallen in de interne meetlat met de verwachting van de eigenaar, twee rondes hoofdversie tegen twee rondes kandidaat.

## 6. Stap 3: handelingen

De handelingsstap van stap 1 is de minimale vorm. Stap 3 haalt het zoekgereedschap uit de lijst die de schrijver ziet, omdat het pad zelf al zoekt, en legt vast welke handelingen een medewerker via de chat mag vragen. Pas bouwen als het gebruik erom vraagt: negen aanvragen in vier weken is geen reden [gemeten].

## 7. Wat bewust niet gebouwd wordt

| Wat | Waarom niet | Wanneer wel |
|---|---|---|
| De interne chat het model zelf laten kiezen wanneer het zoekt en welke gereedschappen het pakt | Het geeft de winst van 24 september op, en bewijs dat een klein model die regie betrouwbaar voert ontbreekt | Als een meting op de interne meetlat laat zien dat een vrij pad gelijk of beter scoort |
| Een straf op herhaalde woorden | Productnamen en instellingen komen terecht terug, en de doorgeeflaag geeft de instelling voor dit model waarschijnlijk niet door [aanname] | Niet |
| Minder passages of een lagere temperatuur tegen de ontsporing | Gemeten zonder effect | Niet voor dit probleem |
| De 67 vertakkingen in één keer opruimen | Een verbouwing zonder gedragswinst | Per vertakking, zodra een stap van dit ontwerp haar overbodig maakt |

## 8. Wat nog onbekend is

- Of de voorkant precies deze tien gereedschappen onder deze namen meestuurt; het nagebouwde verzoek kwam op 162 tokens na overeen met productie.
- Of de voorkant een ongevraagde opslagaanvraag uitvoert. Bij het antwoord van 17 november is geen opslag in de logboeken te zien; de andere ontspoorde antwoorden zijn daar niet op nagekeken.
- Of andere ontspoorde beurten hetzelfde patroon volgen; er is er één nagebouwd.
