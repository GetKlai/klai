# Hoe lang andere systemen gesprekken bewaren

> Last updated: 2026-11-12
> Status: Research, de basis onder de bewaartermijn van negentig dagen in [conversation-retention-and-learning.md](../architecture/conversation-retention-and-learning.md). Dit document zegt wat andere systemen doen en wat toezichthouders eisen; wat Klai doet staat in dat document.
> Herkomst: één zoekronde in de documentatie van de leveranciers zelf op 12 november 2026. Drie uitspraken zijn daarna in de bron teruggelezen: Intercom Fin ("retained indefinitely"), Ada (transcripten na twee jaar verwijderd, "unless personal details are removed") en Microsoft Copilot Studio (een taak verwijdert transcripten ouder dan dertig dagen, en de klant kan dat wijzigen). De rest komt uit die ene zoekronde en is niet apart nagelezen.

## Vraag

Hoe lang bewaren systemen die een chat met bezoekers aanbieden of die gesprekken beoordelen de tekst van een gesprek, en wat blijft er daarna over? Bedoeld om een bewaartermijn te kiezen die lang genoeg is om van gesprekken te leren en kort genoeg om geen uitzonderingen nodig te hebben.

## Wat leveranciers doen

De kolom "Pagina" noemt de documentatiepagina waar de uitspraak staat, als omschrijving van het onderwerp en niet als exacte titel; URL's staan er bewust niet bij.

| Product | Gesprek | Wat daarna blijft | Pagina |
|---|---|---|---|
| Intercom Fin | onbeperkt bewaard ("retained indefinitely") tot de klant zelf verwijdert; teruglezen in de bron | de cijfers blijven standaard ook na verwijderen | de pagina over gegevensbewaring en verwijderen van Fin-gesprekken |
| Ada | twee jaar, daarna verwijderd "unless personal details are removed"; teruglezen in de bron | langer als de persoonsgegevens eruit zijn | de pagina over bewaring van transcripten |
| Microsoft Copilot Studio | een taak verwijdert transcripten ouder dan dertig dagen; de klant kan de termijn wijzigen; teruglezen in de bron | afloop en tevredenheid per gesprek | de pagina over gespreksopslag en transcripten |
| Zendesk | de klant stelt een verwijderschema in, dertig dagen als voorbeeld | niet gevonden | de pagina over verwijderschema's voor gesprekken |
| Langfuse en LangSmith (meetgereedschap) | kort; bij LangSmith veertien dagen in de basis | wat in een testset is gezet, blijft onbeperkt | de pagina's over bewaartermijnen van traces |

## Drie patronen

1. De kortste standaard die gevonden is, is dertig dagen. Onbeperkt bewaren komt voor, tot de klant zelf verwijdert.
2. Wie van gesprekken leert, scheidt de ruwe gesprekken (kort) van een bewust gekozen testset (lang). Bij de meetgereedschappen is dat de testset die buiten de termijn valt.
3. Toezichthouders noemen geen termijn maar eisen doel en noodzaak, waarbij afgeschermde vrije tekst een persoonsgegeven blijft. Van de Nederlandse toezichthouder is hierover niets gevonden.

Het derde patroon is de reden dat de testset van de meetlat geen afgeschermde kopieën bewaart maar nieuw geschreven gevallen.

## Beperkingen

Eén zoekronde met drie teruggelezen bronnen. De termijnen van Zendesk, Langfuse en LangSmith zijn niet teruggelezen en kunnen per abonnement verschillen. Wat de leveranciers standaard instellen is niet hetzelfde als wat de wet vraagt.
