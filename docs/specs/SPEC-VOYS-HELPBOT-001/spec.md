---
id: SPEC-VOYS-HELPBOT-001
version: "0.5.0"
status: built, reviewed, open items recorded
created: 2026-09-04
updated: 2026-09-04
author: Claude (Opus 5), commissioned by Mark Vletter
priority: high
tenant_scope: Voys only (pilot)
related:
  - docs/research/help-page-chatbot-voys.md (research basis; gap IDs G-1..G-16 referenced below)
  - SPEC-PRIVACY-QUERY-SHADOW-001 (REQ-8 telemetry gating that REQ-1 inherits)
  - SPEC-WIDGET-002 (widgets as a first-class domain; this SPEC extends that surface)
  - SPEC-MCP-RETRIEVAL-001 (REQ-9 caller_client_id convention reused by REQ-1)
implementation_branch: feat/voys-helpbot-integratie
---

# HISTORY

| Version | Date | Change |
|---|---|---|
| 0.5.0 | 2026-09-04 | Independent holistic review (Fable) against the intent rather than the checklist. It found the measurement instrument inverted: REQ-5 labelled a single good exchange 'abandoned' and any conversation ending on an answer 'resolved', so the label tracked turn count, not outcome — and with thumbs at 2-8% response those two rules decided nearly every row. The defect came from brief A.6, i.e. from the same party that wrote the brief and approved the result. Fixed by refusing to read silence as a signal in either direction; a large 'unknown' share is now the honest reading and the argument for the LLM-as-judge pass. Also fixed: a red CI gate nobody had run (the vendored prompt copy lagged the canonical one) and an appointment promise that shipped unconditionally to every tenant. Corrected an overclaim in the research: REQ-2 relieves the mint limit, it does not remove the 60 rpm chat limit, so G-14 is partial, not void. |
| 0.4.0 | 2026-09-04 | All eight requirements built and merged. REQ-4 was tuned a second time after Mark supplied the official brand documentation: eight principles turned out to be independently confirmed by the measurement, one contradiction (apologies) was resolved by register rather than by picking a side, and three things the help pages could not show were added. REQ-7 shipped with a sharper dividing line than specified — one decidable test instead of a list. One medium defect recorded rather than fixed: a broad-mode answer lands in the `escalated` outcome bucket. |
| 0.3.0 | 2026-09-04 | REQ-6 rewritten before implementation. The escalation contract flipped twice on the same day and the reason matters for anyone reading the code: v0.2.0 forbade offering a human at all, because the only handoff (HubSpot) is pinned to tenant `getklai` (G-1). Mark then corrected the premise — an API integration with the support partner is coming that will offer appointment booking *inside* the chat, and until it lands we redirect to the partner's existing booking module. So the bot may offer an appointment again, but must not name a URL itself; the widget owns the link. REQ-7 (broad-mode consent switch) added from Mark's observation that non-strict answers know more but are less certain. |
| 0.2.0 | 2026-09-04 | REQ-2 (facade) added, replacing "raise the mint limit" as the answer to G-14 after online research: 3-10% of visitors ever open a chat widget, so 90-97% of session-token mints are waste. REQ-4 (support prompt) written with an explicit ban on offering a human, matching the then-known constraint. |
| 0.1.0 | 2026-09-04 | Initial scope from the research document. Ordering principle set with Mark: measurement before configuration before go-live, because without it we cannot tell whether the pilot worked. |

---

# 1. Why

`help.voys.nl` has no chat assistant today; the escalation route at the bottom
of every article is the telephone. The Voys support content is already crawled
into the tenant's KB, and the retrieval stack that serves it is the strong part
of the platform. What is missing is not retrieval but everything around it: the
widget path lacks the answer-policy layers the internal chat has, produces no
feedback signal, feeds no editorial loop, and speaks in an internal register.

This SPEC covers the code needed before a Voys-only pilot can start. It does
not cover the pilot configuration itself, which is admin work in the portal and
requires someone who knows the Voys content.

# 2. Scope

**In scope:** the widget/partner chat path (`path B`), the embeddable widget,
and the shared prompt library — for the Voys tenant as the first user.

**Explicitly out of scope**, and why:

| Not doing | Why |
|---|---|
| Multi-tenant HubSpot handoff (G-1) | Largest dev item in the research doc; the booking redirect (REQ-6) covers the pilot need |
| Porting path A's full answer-policy layer to path B (G-2) | Structural: path B talks to LiteLLM with the master key, so the hook can never scope policy to it (see §5.4 of the research doc). Needs its own SPEC |
| Widget eval harness (G-6) | Wait until REQ-1 and REQ-3 produce real data to evaluate against |
| Server-side conversation state (G-13) | Client-owned threading is the platform contract; changing it is not a pilot concern |

# 3. Requirements

Status legend: **done** = merged into the implementation branch with gates
green and reviewed; **wip** = under construction; **open** = specified only.

## REQ-1 — Register knowledge gaps from the widget path · done · G-15

The gaps dashboard must see the questions real visitors ask, not only internal
chat. Gap classification and registration run on the widget path.

- portal-api is itself the receiver of `/internal/v1/gap-events`, so the
  in-process path MUST NOT loop back over HTTP. The write path is shared.
- The SPEC-PRIVACY-QUERY-SHADOW-001 REQ-8 gating moves into the shared service
  so the widget path inherits it: `telemetry_level` is re-fetched from
  `portal_orgs` and never taken from the caller; anything not explicitly `full`
  redacts the query text.
- Registration is fire-and-forget on its own tenant-scoped session and can
  never fail or delay the chat answer.
- Widget rows carry `caller_client_id='widget-chat'`.

**Known limitation, accepted:** `nearest_kb_slug` stays NULL on this path
because evidence-pack chunks carry no `kb_slug`, so taxonomy classification
never triggers for widget rows. Gaps appear in the dashboard but uncategorised —
which is exactly the feature that turns a gap list into an editorial plan. Worth
a follow-up.

Brief: appendix A.1 · Commit `83247a5`

## REQ-2 — Defer the config fetch behind a facade bubble · done · G-16, closes G-14

The widget minted a session token on every page view. Bubble mode now renders a
network-free facade; the config request waits for the first click. Inline mode
is deliberately unchanged. A failed fetch surfaces an alert and a second click
retries; once resolved it never fetches again.

An optional `data-primary-color` on the script tag lets the facade start in the
customer's brand colour. The value comes from a third-party page, so it is
accepted only as strict hex and otherwise dropped.

This supersedes raising the mint limit: with only openers minting, the limit is
relieved by roughly 10-30x and the capacity question stops being a go-live
blocker.

Brief: appendix A.2, appendix A.3 · Commit `a401453`

## REQ-3 — Thumbs feedback on assistant answers · done · G-3

Visitors can rate an assistant answer up or down, withdraw it, or switch it; the
choice survives a reload.

- The row id of a message is never exposed to the browser. The widget generates
  a random per-turn identifier, accepted server-side only in strict hex.
- The rating UPDATE matches on that identifier plus the caller's own org,
  session and widget in one statement; a foreign turn id matches nothing and
  gets a non-disclosing 404.
- DB constraints restrict ratings to `thumbsUp`/`thumbsDown` on assistant rows.

**Open defect, medium:** the unique index on `turn_id` is global across tenants
rather than scoped per conversation. Practical risk is negligible (128-bit
random value; a collision only loses one fire-and-forget audit row and is
invisible to the visitor), but the correct scope is per conversation.

Brief: appendix A.4 · Commit `cd31827`

## REQ-4 — Customer-facing support prompt profile · done · G-5

`SUPPORT_CHAT_SYSTEM_PROMPT` alongside GROUNDED/GENERAL/OPEN_KB/META, reusing
the shared language-detection preamble so the three guards cannot drift.

Behaviour that differs from GROUNDED: customer register (je/jij), no use of the
word "kennisbank" toward a visitor, procedures as numbered steps keeping the
article's own button and field labels, at most one clarifying question, and a
ban on committing to prices, delivery times, goodwill, refunds, contract terms
or outage status on the company's behalf.

Opt-in per widget. **Verification requirement:** the existing four profiles must
stay byte-identical — verified by comparing resolved constant values, not the
diff, because the change reorders the file.

Brief: appendix A.5 · Commit `6af411d`

## REQ-5 — Conversation outcome label · done · G-4

`widget_conversations.outcome` in `resolved|escalated|abandoned|unknown|NULL`,
derived by a background pass from signals we already have, and exposed as a
distribution on the stats endpoint without changing existing fields.

The derivation is explicitly a heuristic and must be documented as such: it says
where a conversation ended, not whether the visitor's problem was solved.

**Corrected after review.** The first implementation followed brief A.6, which
told it to read a single exchange as a bounce and any conversation ending on an
answer as resolved. Those two rules made the label track turn count: one good
answer scored 'abandoned', three bad ones the visitor gave up on scored
'resolved'. Silence is no longer read as a signal in either direction. Only an
explicit thumbs-up gives 'resolved'; only an unanswered question, once the
conversation has gone quiet, gives 'abandoned'. Everything else is 'unknown',
and a large unknown share is the point — it is the case for the LLM-as-judge
pass, not a hole to fill with a guess. Volume
metrics alone steer toward "the visitor gave up", which the market literature
puts 20-30 points away from real resolution.

Brief: appendix A.6 · Commit `c9f6f77`

## REQ-6 — AI disclosure and appointment escalation · done · G-10, G-1 (partial)

**Disclosure.** The empty state shows, before the visitor types, that this is an
AI assistant that knows a lot about Voys, cites its sources where it can, and
can offer an appointment with a person. This line is NOT governed by
`hide_disclaimer`: that flag is a white-label option and a legally required
notice must not sit behind it. Screen-reader announced, not merely visible.
EU AI Act Article 50 has applied since 2026-08-02 and requires the notice to be
perceivable in the interaction itself, at first contact.

**Escalation.** The bot cannot transfer the chat and must not suggest it, but
may offer to book an appointment. It never names a phone number, e-mail address
or URL — the widget owns the link. Offered on frustration, repeated complaints,
cancellations, outages, pricing and contract questions, and when the answer is
honestly not in the help articles.

**Redirect, explicitly interim.** A validated, optional booking URL in the
widget config; http/https only, since this is admin input landing in a visitor's
`href`. Absent, no button and no behaviour change. To be replaced by the support
partner's API integration once that lands.

Brief: appendix A.7 · Commit `d55ec67`

## REQ-7 — Consent-gated broad mode · done

When the help articles do not cover a question, the bot may offer to answer from
general knowledge instead of stopping at "I can't find this". The visitor must
accept explicitly, in-conversation, and the offer must state that those answers
are less certain.

**The dividing line is subject matter, not confidence.** Broad mode may cover
general telecom and IT knowledge — what DECT is, how number portability works in
the Netherlands. It may NEVER produce Voys-specific facts: no prices, features,
settings, availability, or "at Voys you do it like this". That is the failure
mode this feature would otherwise introduce: sounding certain about the product
while having invented it.

The answer must be visibly marked as general knowledge rather than sourced from
the help articles — otherwise it pollutes REQ-1 and REQ-5, where such turns
would count as answered from the KB.

Prior art: path A has a Strict/Open toggle the user sets in advance. That model
does not transfer — a help-page visitor does not know what "strict" means and
will not go looking for a setting. The offer has to come from the bot, at the
moment it is relevant.

**As built.** The dividing line became one decidable test rather than a list:
*could this sentence be written unchanged by any other phone provider?* Yes →
allowed; only true here → refused, "even if you are sure you know it, even if
your training data seems to confirm it". Blending is called out explicitly
("most providers, including this one, ..."), and a mixed question gets the
world half answered and the us half refused. Retrieval still runs first every
turn. Both measurement traps are handled: the gap event keeps firing on a broad
turn, and the outcome label does not read it as sourced.

**Open defect, medium.** A broad answer lands in the `escalated` bucket, even
when the visitor rated it up. The intent is right — the articles could not
answer — but the label reads as "a human got involved", which did not happen.
It inflates the escalation rate and hides real handoffs in the same pile. The
`outcome` CHECK allows only four values, so a fifth needs a migration; the
alternative is documenting what `escalated` means on the dashboard. The gap
registry carries the content signal either way, so this is a reporting-clarity
problem, not a lost signal.

Brief: appendix A.9 · Commit op branch `feat/broad-mode-consent`

## REQ-8 — Voys tone of voice · done

A written profile derived from the actual Voys help articles and marketing
pages, with every claim backed by a quote and a URL, distinguishing the
marketing register from the support register and stating which one the bot
follows. Delivers a block of at most 25 imperative Dutch lines that can go
straight into REQ-4's prompt.

Brief: appendix A.8 · Commit `2335ed6` · Levert `docs/research/voys-tone-of-voice.md`

# 4. Settled decisions — do not re-open

**Escalation goes to the support partner, never to a phone number.** Today via
their booking form (a redirect from the widget); later via their API so the
appointment can be booked inside the chat. The bot never states a phone number
or e-mail address — not even one that appears verbatim in a help article it
just cited. This was restated three times in the same session, so it is written
down here: the tone-of-voice profile observes that Voys' own articles sometimes
close with the support number, and that observation does not transfer to the
bot. Article style and product policy are different things.

# 5. Other decisions worth preserving

**Measurement before configuration.** REQ-1 and REQ-3 come before the pilot is
switched on, because a bot you cannot measure teaches you nothing and the
editorial loop is the durable value.

**The capacity question was reframed, not answered.** The first analysis called
the per-widget rate limits a blocker on mechanism alone, without knowing Voys'
actual traffic — an overstatement. The facade (REQ-2) removes the question
rather than tuning the number.

**Reviews found nothing above medium.** Across REQ-1 to REQ-4: 0 critical, 0
high. Per the ">= high only" gate, nothing was fixed; the medium and low items
are recorded here rather than in the code as TODOs. Two items in REQ-1's shared
service were checked against `HEAD` and found pre-existing (raw query text
reaching taxonomy classification in shadow mode; an unretained
`asyncio.create_task`), so they are backlog, not scope.

# 6. Verification

All gates run by Claude on the merged branch, not taken from builder reports.

| Gate | Result |
|---|---|
| `pytest tests/ -q` (portal backend) | 3867 passed, 0 failed (baseline 3796) |
| chat-prompts tests | 137 passed |
| `npm run build` (widget) | exit 0 |
| `npm run check-size` | 36.2 kB gzipped, limit 200 kB |
| portal frontend widget tests | 21 passed |

Every builder ran under an agent profile that cannot commit, push or stash —
denied at the permission layer, verified by a test that attempted a commit and
was refused. Work therefore always arrives as an unstaged diff for review.

# 7. Open

Recorded by the holistic review and deliberately not fixed here — they need a
decision rather than a patch:

- **The citation firewall discards most of what REQ-4 adds.** Any answer without
  a selected source is replaced by the fixed refusal, and a clarifying question,
  an honest "not in the articles", an apology or an appointment offer has no
  source by definition. A large part of the brand-voice prompt therefore never
  reaches the visitor on this path. This is the concrete cost of the two
  parallel chat paths (§2, research §5.4); the reviewer advises fixing it as a
  requirement on the shared answer-policy layer rather than patching path B.
- **Broad mode is sticky and can degrade later grounded answers.** Consent holds
  for the conversation and also triggers on a soft gap, so a follow-up with
  weak-but-usable chunks is forced broad. Consider hard-gap-only, or per-turn
  consent.
- **Admin preview traffic registers knowledge gaps**, while stats and outcome
  already exclude preview conversations. Small fix, real dashboard pollution.
- **The pilot cannot be configured in the portal**: no UI for `support_mode`,
  `booking_url` or the outcome distribution. API or database only for now.
- **The most-shown sentence of the bot is off-brand.** The fixed refusal reads
  "Neem voor een vast antwoord contact op met de support", which the brand
  document lists under what does not work — and because it bypasses the prompt,
  the tone work cannot reach it.
- **Gap rows carry a 7-day retention** while widget messages get 90, so the
  editorial signal expires faster than the conversations it came from.
- **A broad answer lands in the `escalated` bucket.** Reviewer's counter: no
  migration needed — the marker is in the content, so the stats endpoint can
  count broad answers separately and `derive_outcome` can leave them `unknown`.


- All eight requirements are merged on `feat/voys-helpbot-integratie`. Nothing is pushed.
- Pilot configuration (widget, KB scope, allowed origins, conversation
  starters) — admin work, needs Voys content knowledge.
- **Assumption to verify:** the support partner's booking module is reachable
  from every help article page, not only the index. If it is not, REQ-6 offers
  a route the visitor cannot see, which is worse than offering nothing.
- Baseline measurement of current phone/mail volume before go-live, or the
  pilot's effect cannot be shown afterwards.


---

# Appendix A — de opdrachten zoals de bouwers ze kregen
Dit zijn de briefs verbatim. Ze zijn de feitelijke specificatie die elke
bouw-agent heeft uitgevoerd; de requirements hierboven zijn de samenvatting
ervan. Wie het opgeleverde werk tegen de bedoeling wil leggen, moet deze
teksten lezen en niet alleen §3.

## A.1 REQ-1 · kennisgaten registreren vanaf het widget-pad

```text
Implementeer gap-event-registratie voor het widget/partner-chatpad in de
Klai-monorepo. Werk in de huidige werkmap (een git-worktree).

## Achtergrond (nodig om de opdracht te begrijpen)

Klai registreert "kennisgaten": vragen waar retrieval niets of alleen zwakke
resultaten voor vond. Die belanden in de tabel achter
`klai-portal/backend/app/api/app_gaps.py`, dat een dashboard voedt.

Vandaag vuurt alleen de LiteLLM-hook zulke events af. Zie
`deploy/litellm/klai_knowledge.py` rond regel 1411:

    gap_type = _classify_gap(chunks)
    if gap_type is not None and org_id and user_id:
        _fire_gap_event(org_id=..., user_id=..., query_text=query,
                        gap_type=gap_type, chunks=chunks,
                        retrieval_ms=retrieval_ms, taxonomy_node_ids=...)

Die hook POST'et naar portal-api's endpoint `/internal/v1/gap-events`
(`klai-portal/backend/app/api/internal.py`, functie `create_gap_event`, rond
regel 1322).

Het widget-pad loopt NIET via die hook. Het loopt via
`klai-portal/backend/app/services/partner_chat.py`, functie `retrieve_context`,
die zelf `/retrieve` aanroept op de retrieval-api en de chunks terugkrijgt.
Daar worden op dit moment geen gap-events geregistreerd.

## Wat je moet bouwen

`retrieve_context` in `partner_chat.py` moet, ná een geslaagde retrieval, een
gap-event registreren wanneer `classify_gap` een gat vaststelt.

BELANGRIJK — geen HTTP-loopback. `partner_chat.py` draait binnen hetzelfde
portal-api-proces als `create_gap_event`. Roep dus niet je eigen endpoint over
HTTP aan. Haal in plaats daarvan de schrijf-logica uit `create_gap_event` naar
een herbruikbare servicefunctie (bijvoorbeeld
`klai-portal/backend/app/services/gap_events.py`) en laat zowel het endpoint
als `partner_chat.py` die aanroepen. Het endpoint moet zich daarna exact
hetzelfde gedragen als nu.

Voorwaarden die je NIET mag breken:
- De privacy-gating blijft intact. `create_gap_event` haalt bewust het
  canonieke `telemetry_level` van de org op en respecteert off/shadow/full
  (off = niets opslaan, shadow = query_text vervangen door
  '[REDACTED:shadow]', full = letterlijke tekst). Die gating hoort in de
  gedeelde servicefunctie, zodat het widget-pad hem automatisch erft. Vertrouw
  nooit een door de aanroeper meegegeven niveau.
- Tenant-scoping blijft intact (`set_tenant` / RLS). Het widget-pad kent het
  Zitadel-org-id al; gebruik dat.
- Registratie mag het chat-antwoord nooit blokkeren of laten falen. Faalt het
  wegschrijven, dan log je dat en gaat de chat gewoon door — zoals de rest van
  de telemetrie in dit bestand ook fire-and-forget is.
- Zet `caller_client_id` op een waarde die widget-verkeer onderscheidt van
  LibreChat-verkeer, zodat het dashboard de twee uit elkaar kan houden. Kies
  een korte, duidelijke string en documenteer die keuze in een comment.
- Geen gedragsverandering voor bestaande aanroepers van `retrieve_context`
  buiten deze toevoeging.

## Tests

Schrijf tests in `klai-portal/backend/tests/` die aantonen:
1. Widget-pad met een 'hard' gat registreert een gap-event.
2. Widget-pad met goede resultaten registreert er géén.
3. Bij telemetry_level 'shadow' bevat de opgeslagen query_text de redactie-
   markering en niet de echte vraag.
4. Een fout tijdens het wegschrijven laat de chat gewoon slagen.
Volg de stijl en fixtures van de bestaande tests in die map.

## Gates — zelf draaien en de uitvoer tonen

Vanuit `klai-portal/backend`:
- `python -m pytest tests/ -k "gap" -q`
- `python -m pytest tests/ -q -x` (volledige suite; meld het exacte aantal
  falende tests als er iets rood is, en of dat al rood was vóór jouw wijziging)
- `ruff check app tests` als ruff beschikbaar is

Committen en pushen is geblokkeerd en niet nodig: lever je werk als wijzigingen
in de working tree.
```

## A.2 REQ-2 · widget lui laden met een facade-bubbel

```text
Maak de Klai-chatwidget lui-ladend met een facade-bubbel. Werk in de huidige
werkmap (een git-worktree). Dit raakt alleen `klai-widget/`, niet de backend.

## Probleem

`klai-widget/src/main.ts` haalt in `bootstrap()` meteen bij het laden van de
pagina de widget-configuratie op (`fetchWidgetConfig`), en die respons bevat een
sessietoken. Dat gebeurt bij ELKE paginaweergave, ook als de bezoeker de chat
nooit opent. Uit marktcijfers opent slechts 3-10% van de bezoekers een
chatwidget, dus 90-97% van die aanvragen is verspilling. Gevolgen: onnodige
belasting van een per-widget aanvraaglimiet, en de volledige widget laadt mee op
elke helppagina.

## Wat je moet bouwen

Het standaardpatroon: een facade. Toon bij het laden van de pagina alleen een
lichtgewicht bubbel die eruitziet als de chat. Haal de configuratie op en bouw
de echte chat pas op wanneer de bezoeker die bubbel voor het eerst aanklikt.

Eisen:
1. Bij paginaweergave wordt GEEN netwerkaanvraag naar de widget-config gedaan.
   De facade-bubbel wordt getekend uit wat al lokaal bekend is: de attributen op
   de script-tag en de bestaande CSS-defaults. Toon geen titel of tekst die je
   alleen uit de config kunt weten.
2. Bij de eerste klik: configuratie ophalen, de bestaande initialisatie
   uitvoeren zoals nu, en het chatvenster openen. De bezoeker mag hooguit een
   korte laadindicatie zien; de klik mag niet verloren gaan.
3. Mislukt het ophalen na de klik, dan toont de widget een nette foutmelding in
   plaats van stil te blijven — en een tweede klik probeert het opnieuw.
4. Voor volgende klikken binnen dezelfde paginaweergave wordt niet opnieuw
   opgehaald.
5. De inline-modus (`data-mode="inline"`) heeft geen facade: daar staat het
   chatvenster bewust direct op de pagina. Laat dat gedrag ongewijzigd — laad
   daar dus wél meteen zoals nu.
6. Bestaande functionaliteit blijft intact: shadow DOM, thema en kleuren uit de
   config zodra die geladen is, opgeslagen gesprekken, en de bestaande
   foutcodes die het script nu logt.

Randvoorwaarden:
- Blijf binnen het bundelbudget: `npm run check-size` in `klai-widget/` moet
  slagen (limiet 200 kB gzipped).
- Volg de bestaande codestijl (SolidJS, TypeScript, dezelfde bestandsindeling).
  Geen herschrijving van componenten die je niet hoeft aan te raken.
- Verwijder de code die je vervangt; laat geen oude en nieuwe opstartroute naast
  elkaar staan.

## Gates — zelf draaien en de uitvoer tonen

Vanuit `klai-widget/`:
- `npm run build`
- `npm run check-size`
Als er een typecheck-stap in `package.json` staat, draai die ook.

Beschrijf in je eindrapport expliciet hoe je hebt geverifieerd dat er bij een
gewone paginaweergave geen configuratie-aanvraag meer wordt gedaan.

Committen en pushen is geblokkeerd en niet nodig: lever je werk als wijzigingen
in de working tree.
```

## A.3 REQ-2 (vervolg) · huisstijlkleur op de facade

```text
Kleine, afgebakende fix in de Klai-chatwidget. Werk in de huidige werkmap.

## Probleem

De facade-bubbel (klai-widget/src/components/WidgetFacade.tsx, gemount vanuit
main.ts in bubble-modus) wordt getekend vóórdat de widget-configuratie is
opgehaald. Hij gebruikt daardoor alleen de CSS-defaults, waaronder de standaard
accentkleur. Bij een klant met een eigen huisstijlkleur staat er dus eerst een
bubbel in de Klai-standaardkleur, die verspringt naar de klantkleur zodra
iemand klikt en de configuratie binnenkomt. Dat is zichtbaar en oogt slordig op
de site van een klant.

## Wat je moet bouwen

1. main.ts leest een nieuw attribuut `data-primary-color` van de script-tag
   (naast de bestaande `data-widget-id`, `data-mode`, `data-locale`,
   `data-container`). Staat het er, dan wordt die kleur als CSS-variabele op de
   shadow-root gezet vóórdat de facade rendert, zodat de bubbel meteen de
   goede kleur heeft.
2. Valideer de waarde streng voordat je hem in CSS zet. Accepteer alleen een
   hex-kleur (#rgb, #rrggbb, #rrggbbaa). Alles anders negeer je stil en dan
   val je terug op de bestaande default. Dit is een waarde uit de pagina van
   een derde partij; hij mag nooit als ruwe tekst in een stylesheet belanden.
3. Zodra de configuratie na de eerste klik binnenkomt, wint die zoals nu: de
   bestaande overschrijving van de CSS-variabelen blijft leidend. De
   data-attribuut-kleur is alleen de voorvertoning.
4. Pas de embed-code-generator aan zodat het attribuut wordt meegegeven:
   klai-portal/frontend/src/features/widgets/embed/snippet.ts, functie
   `buildWidgetEmbedSnippet`. Voeg een optionele parameter voor de primaire
   kleur toe en zend `data-primary-color` mee wanneer die er is, in dezelfde
   stijl als de bestaande `data-title` en `data-welcome`. Pas de aanroepers van
   die functie aan zodat de ingestelde kleur van de widget wordt doorgegeven.

## Randvoorwaarden

- Alleen dit. Geen andere verbeteringen, geen herformattering.
- De inline-modus verandert niet.
- Verandert er niets aan het gedrag wanneer het attribuut ontbreekt.
- Volg de bestaande codestijl.

## Gates — zelf draaien en de uitvoer tonen

Vanuit klai-widget/: `npm run build` en `npm run check-size`.
Vanuit klai-portal/frontend/: draai de testopdracht uit package.json die bij de
widget-embed hoort (zoek naar bestaande tests voor snippet.ts of de EmbedTab) en
de typecheck. Meld exact welke commando's je draaide en met welke exitcode.
```

## A.4 REQ-3 · duim omhoog / omlaag

```text
Bouw feedbackknoppen (duim omhoog / duim omlaag) in de Klai-chatwidget, en sla
die feedback op. Werk in de huidige werkmap.

## Waarom

Een publieke helpdesk-bot zonder feedbackknop laat je in het ongewisse over
welke antwoorden goed waren. Het endpoint `/partner/v1/feedback` bestaat al
(`klai-portal/backend/app/api/partner.py`, rond regel 2289) maar is bedoeld voor
partner-API-sleutels: de widget-sessie krijgt in
`klai-portal/backend/app/api/partner_dependencies.py` expliciet
`permissions={"chat": True, "feedback": False, ...}`. In de widget-UI bestaan
de knoppen helemaal niet.

## Wat je moet bouwen

Backend:
1. Een endpoint waarmee een widget-sessie feedback op één assistent-antwoord kan
   geven. Kies bewust: ofwel het bestaande `/partner/v1/feedback` openstellen
   voor widget-tokens, ofwel een apart endpoint op de widget-route. Motiveer je
   keuze in een comment. Let op dat het bestaande endpoint een
   `require_permission(auth, "feedback")` heeft en gedrag kent dat je niet mag
   breken voor bestaande partner-sleutels.
2. Opslag van de beoordeling bij het betreffende bericht. De tabel
   `widget_messages` (zie `klai-portal/backend/app/models/widgets.py`) heeft nu
   geen kolom daarvoor. Voeg er een toe met een Alembic-migratie in
   `klai-portal/backend/alembic/versions/`, in dezelfde stijl als de bestaande
   migraties. Alleen assistent-berichten kunnen een beoordeling dragen.
3. Tenant-scoping en RLS blijven intact: een widget-sessie mag uitsluitend
   feedback geven op berichten uit haar eigen gesprek, van haar eigen org.
   Controleer dat expliciet en schrijf er een test voor die aantoont dat een
   poging op een vreemd bericht wordt geweigerd.
4. Rate limiting: feedback mag geen nieuw misbruikkanaal worden. Sluit aan bij
   het bestaande sliding-window-patroon in
   `app/services/partner_rate_limit.py`.

Widget:
5. Onder elk assistent-antwoord twee knoppen: duim omhoog en duim omlaag.
   Toegankelijk (aria-label, toetsenbedienbaar), en zichtbaar als gekozen nadat
   erop is geklikt. Nogmaals klikken op dezelfde knop trekt de beoordeling in;
   op de andere klikken wisselt hem.
6. Verstuur de beoordeling naar het endpoint uit stap 1. Mislukt dat, dan blijft
   de UI bruikbaar en verschijnt er geen storende foutmelding — dit is
   secundair aan het gesprek zelf.
7. Bewaar de gekozen beoordeling in dezelfde opslag als de gespreksgeschiedenis
   (`klai-widget/src/store/chat.ts`), zodat hij na herladen zichtbaar blijft.
8. Nederlandse en Engelse labels in `klai-widget/src/i18n/labels.ts`, in
   dezelfde stijl als de bestaande.

## Randvoorwaarden

- Verander niets aan het bestaande gedrag voor partner-API-sleutels.
- Blijf binnen het bundelbudget: `npm run check-size` moet slagen.
- Volg de bestaande codestijl van elk bestand dat je aanraakt.

## Gates — zelf draaien en de uitvoer tonen

- `klai-portal/backend`: `.venv/bin/python -m pytest tests/ -q` (meld het exacte
  aantal geslaagd/gefaald, en of iets al rood was vóór je wijziging)
- `klai-widget`: `npm run build` en `npm run check-size`
```

## A.5 REQ-4 · helpdesk-promptprofiel

```text
Bouw een helpdesk-promptvariant voor de Klai-chatwidget. Werk in de huidige
werkmap.

## Waarom

De gedeelde chat-prompts in `klai-libs/chat-prompts/klai_chat_prompts/__init__.py`
zijn geschreven voor een interne kenniswerker. De docstring zegt het zelf:
Klai is "an internal-team tool, not a customer-support surface". Voor een
publieke helppagina klopt dat niet:

- De toon is "a senior colleague", zonder begroeting of afsluiting. Een
  klant op een helppagina verwacht klantvriendelijk, niet collegiaal-kortaf.
- Bij een ontbrekend antwoord zegt de bot "Dat staat niet in de kennisbank".
  Een websitebezoeker weet niet wat een kennisbank is.
- Er is geen instructielaag voor escalatie of voor doorvragen bij een vage
  vraag.
- Er staat niets over dat de bezoeker met een AI praat.

## Wat je moet bouwen

1. Een nieuw promptprofiel in dezelfde library, naast de bestaande
   GROUNDED / GENERAL / OPEN_KB / META, bijvoorbeeld
   `SUPPORT_CHAT_SYSTEM_PROMPT`. Het hergebruikt de bestaande
   taaldetectie-preamble ongewijzigd — die drie waarborgen mogen niet
   uiteenlopen tussen profielen, dat is expliciet de reden dat ze in een
   gedeelde constante staan.
2. Inhoudelijk moet het profiel, in vergelijking met GROUNDED:
   - klantvriendelijk maar zakelijk zijn, geen emoji, je/jij-vorm;
   - bij een ontbrekend antwoord klantentaal gebruiken ("Ik vind dit niet
     terug in onze helpartikelen") in plaats van het woord kennisbank;
   - bij een vage vraag maximaal één korte verduidelijkingsvraag stellen;
   - meerdere vragen in één bericht genummerd en apart beantwoorden (dat
     gedrag bestaat al in GROUNDED — hergebruik het, dupliceer het niet);
   - procedurele antwoorden als genummerde stappen geven met de labels zoals
     ze in het bronartikel staan;
   - geen toezeggingen doen namens het bedrijf over levertijden, prijzen,
     coulance of storingen;
   - de bestaande regels rond bronvermelding ongewijzigd laten: het model
     schrijft zelf geen URLs of citatienummers, de applicatie voegt bronnen
     achteraf toe.
3. Een variant van de standaardweigering. `no_citable_sources_message` geeft nu
   "Ik kan dit niet betrouwbaar beantwoorden op basis van de beschikbare
   kennisbronnen." Voeg een helpdesk-variant toe in dezelfde tweetalige stijl,
   zonder het woord kennisbank/kennisbronnen, en met het aanbod om contact op te
   nemen met support. Breek de bestaande functie niet: bestaande aanroepers
   moeten exact dezelfde tekst blijven krijgen.
4. Laat `klai-portal/backend/app/services/partner_chat.py` het nieuwe profiel en
   de nieuwe weigering gebruiken wanneer de widget daarom vraagt. Kies zelf hoe
   dat geschakeld wordt — een veld in `widget_config` ligt voor de hand, in de
   stijl van de bestaande vlaggen zoals `show_sources` en `hide_disclaimer` —
   en zorg dat het gedrag zonder die vlag exact blijft zoals het nu is.

## Belangrijk: wat je NIET doet

- Geen escalatie-instructie die een medewerker aanbiedt. Die knop werkt in deze
  opstelling niet. Schrijf in plaats daarvan dat de bot bij frustratie,
  herhaalde klachten, opzeggingen, storingen of prijs- en contractvragen
  verwijst naar de supportafdeling via de contactgegevens op de site — zonder
  een specifiek telefoonnummer of e-mailadres te noemen, want die staan niet in
  de prompt en mogen niet verzonnen worden.
- Geen wijziging aan GROUNDED, GENERAL, OPEN_KB of META. Die worden door de
  app-chat gebruikt en moeten letterlijk gelijk blijven. Er is een CI-lint die
  kopieën van deze constanten elders in de monorepo afkeurt; maak dus geen
  duplicaat.

## Gates — zelf draaien en de uitvoer tonen

- `klai-libs/chat-prompts`: de bestaande tests in `tests/`
- `klai-portal/backend`: `.venv/bin/python -m pytest tests/ -q` (exact aantal
  geslaagd/gefaald, en of iets al rood was vóór je wijziging)
```

## A.6 REQ-5 · uitkomstlabel per gesprek

```text
Voeg een uitkomstlabel toe aan widget-gesprekken. Werk in de huidige werkmap.
Dit is backend-werk; raak de widget-frontend niet aan.

## Waarom

Het widget-dashboard telt nu alleen volume: aantal gesprekken, aantal berichten,
topvragen, verdeling per uur (`klai-portal/backend/app/api/admin_widgets.py`,
het stats-endpoint). Daarmee weet je hoeveel er gepraat is, maar niet of het
ergens toe leidde. Voor een publieke helpdesk is dat de kern: het verschil
tussen "de bezoeker had genoeg aan het antwoord" en "de bezoeker gaf het op" is
in de markt 20 tot 30 procentpunt, en wie alleen volume meet stuurt richting het
tweede.

## Wat je moet bouwen

1. Een kolom `outcome` op `widget_conversations`
   (`klai-portal/backend/app/models/widgets.py`), met een Alembic-migratie in
   `klai-portal/backend/alembic/versions/` in de stijl van de bestaande
   migraties — inclusief een post-deploy SQL-bestand als de tabel dat patroon
   volgt (kijk hoe dat bij eerdere kolommen op deze tabel is gedaan).
   Toegestane waarden, afgedwongen met een CHECK: 'resolved', 'escalated',
   'abandoned', 'unknown', of NULL zolang er nog niets bepaald is.
2. Een service die het label afleidt uit gegevens die we al hebben. Verzin geen
   nieuwe signalen; gebruik wat er staat:
   - 'escalated' wanneer er een handoff-sessie aan het gesprek hangt, of
     wanneer het laatste assistent-antwoord de bezoeker naar support verwees;
   - 'abandoned' wanneer het gesprek eindigt op een vraag van de bezoeker
     zonder assistent-antwoord daarna, of wanneer er precies één beurt is
     geweest en daarna niets meer binnen het tijdvenster;
   - 'resolved' wanneer het gesprek eindigt op een assistent-antwoord met een
     positieve beoordeling, of eindigt op een assistent-antwoord zonder dat de
     bezoeker daarna nog een vraag stelt binnen het tijdvenster;
   - 'unknown' als geen van deze regels sluit.
   Documenteer de regels in de docstring, met de expliciete waarschuwing dat dit
   een heuristiek is en geen oordeel over of het probleem echt opgelost is.
3. Een achtergrondtaak die het label zet voor gesprekken die minstens een
   ingesteld aantal minuten stil zijn. Volg het patroon van
   `app/services/widget_messages_retention.py`: periodieke lus, gescopeerd per
   tenant, fouten worden gelogd en breken de lus niet.
4. Het stats-endpoint uitbreiden met de verdeling per uitkomst over de gekozen
   periode. Bestaande velden blijven ongewijzigd, zodat de frontend niet breekt.

## Randvoorwaarden

- Tenant-scoping en RLS blijven intact; werk per org, nooit cross-tenant.
- Geen wijziging aan de widget-frontend.
- Geen wijziging aan bestaande stats-velden.
- Tests voor de afleidingsregels: minstens één per uitkomstwaarde, plus één die
  aantoont dat een gesprek van een andere org nooit wordt geraakt.

## Gates — zelf draaien en de uitvoer tonen

Vanuit `klai-portal/backend`: `.venv/bin/python -m pytest tests/ -q`. Meld het
exacte aantal geslaagd/gefaald en of iets al rood was vóór je wijziging.
```

## A.7 REQ-6 · AI-vermelding en afspraak-escalatie

```text
Twee samenhangende wijzigingen: een verplichte AI-vermelding in de widget, en
een aangepaste escalatie-instructie in de helpdesk-prompt. Werk in de huidige
werkmap.

## Achtergrond

De widget krijgt een publieke helppagina als podium. Sinds 2 augustus 2026 eist
artikel 50 van de EU AI Act dat iemand die met een AI-systeem interacteert dat
wéét, en dat die mededeling waarneembaar is in de interactie zelf, bij het
eerste contact. Een zin in de voorwaarden of een vage "assistent" volstaat
uitdrukkelijk niet.

De widget heeft nu een `disclaimer`-regel onderaan
(`klai-widget/src/i18n/labels.ts`: "AI-antwoorden kunnen fouten bevatten...")
die per widget uitgezet kan worden met de vlag `hide_disclaimer` — dat is een
white-label-optie. Een wettelijk verplichte mededeling hoort niet achter zo'n
vlag te zitten.

## Deel 1 — AI-vermelding in de widget

1. Toon in de lege begintoestand van het chatvenster, dus vóórdat de bezoeker
   iets typt, een duidelijke regel dat dit een AI-assistent is. Deze regel is
   NIET afhankelijk van `hide_disclaimer`: die vlag mag de bestaande
   nauwkeurigheidsdisclaimer blijven verbergen, maar niet deze mededeling.
   Documenteer dat onderscheid in een comment met de reden.
2. De tekst komt in `labels.ts` in Nederlands en Engels, in dezelfde stijl als
   de bestaande labels. Nederlandse strekking, letterlijk zo bedoeld:
   dat je met een AI-assistent praat, die veel weet over Voys, die bij zijn
   antwoorden de bronnen erbij zet waar dat kan, en dat je een afspraak kunt
   inplannen met een medewerker die je persoonlijk verder helpt als je er samen
   niet uitkomt.
   Houd het kort: maximaal drie zinnen, geen emoji.
3. Het bestaande, per widget instelbare `welcome_message` blijft bestaan en
   blijft configureerbaar. De AI-vermelding staat ernaast, niet in plaats
   daarvan, zodat een klant zijn eigen welkomsttekst kan schrijven zonder de
   mededeling te kunnen weglaten.
4. Toegankelijkheid: de mededeling moet door een schermlezer worden voorgelezen
   bij het openen van het venster, niet alleen visueel zichtbaar zijn.

## Deel 2 — escalatie-instructie in de prompt

In `klai-libs/chat-prompts/klai_chat_prompts/__init__.py` staat sinds kort
`SUPPORT_CHAT_SYSTEM_PROMPT`. De sectie "Escalation and frustration" zegt nu dat
de bot geen mens kan aanbieden en dat ook niet mag voorstellen. Dat wordt
aangepast.

Situatie: er komt een API-koppeling waarmee we het inplannen van een afspraak
straks ín de chat aanbieden. Tot die er is, verwijzen we door naar de
boekingsmodule van onze supportpartner via een redirect. De bot moet dus een
afspraak kunnen aanbieden, en de widget moet die doorverwijzing kunnen
uitvoeren.

Herschrijf de sectie zo dat:
- de bot NIET kan doorverbinden met een medewerker in dit gesprek en dat ook
  niet suggereert;
- de bot wél mag aanbieden om een afspraak in te plannen met een medewerker die
  persoonlijk verder helpt;
- de bot dat aanbod formuleert als een handeling die de bezoeker kan doen, zonder
  zelf een telefoonnummer, e-mailadres of URL te noemen — de widget levert de
  knop of link, niet de prompttekst;
- dat aanbod komt bij frustratie, herhaalde klachten, opzeggingen, storingen,
  prijs- en contractvragen, en wanneer de bot het antwoord na een eerlijke
  poging niet in de helpartikelen kan vinden.

## Deel 3 — de doorverwijzing zelf

De widget moet die afspraak-doorverwijzing kunnen tonen als een knop of link die
naar een configureerbare URL gaat (de boekingsmodule van de supportpartner).

- Nieuw veld in de widget-configuratie, in de stijl van de bestaande velden
  zoals `show_sources` en `collect_user_info`: een optionele
  boekings-URL. Staat die niet ingesteld, dan toont de widget geen
  afspraak-knop en verandert er niets aan het huidige gedrag.
- De URL wordt server-side gevalideerd voordat hij wordt uitgeleverd: alleen
  http/https, geen `javascript:` of andere schema's. Deze waarde komt uit
  beheerdersinvoer en belandt in een `href` in de browser van een bezoeker.
- De knop opent in een nieuw tabblad met `rel="noopener noreferrer"`.
- Nederlandse en Engelse labels in `labels.ts`, in de bestaande stijl.
- Dit is expliciet een tussenoplossing tot de API-koppeling er is; zet dat in
  een comment bij het configuratieveld zodat het later gericht vervangen kan
  worden.

Verander niets aan GROUNDED, GENERAL, OPEN_KB of META — die worden door de
interne chat gebruikt en moeten letterlijk gelijk blijven. Controleer dat na
afloop door de uiteindelijke waarden van die constanten te vergelijken, niet
alleen de diff.

## Gates — zelf draaien en de uitvoer tonen

- `klai-widget`: `npm ci` als node_modules ontbreekt, dan `npm run build` en
  `npm run check-size`
- `klai-libs/chat-prompts`: de bestaande tests
- `klai-portal/backend`: `.venv/bin/python -m pytest tests/ -q`, met het exacte
  aantal geslaagd/gefaald
```

## A.8 REQ-8 · tone of voice van Voys

```text
Onderzoek de tone of voice van Voys en lever een bruikbaar schrijfprofiel op.

## Doel

We bouwen een AI-helpbot voor de Nederlandse helppagina's van Voys. De bot moet
klinken als Voys, niet als een generieke chatbot. Jij levert het materiaal
waarmee we de instructies van die bot kunnen aanscherpen.

## Bronnen

Bekijk de echte teksten, niet je aannames. Gebruik de browser en/of websearch:
- `https://help.voys.nl/` en een ruime steekproef van de onderliggende
  helpartikelen — dit is de BELANGRIJKSTE bron, want de bot schrijft in dat
  register.
- `https://www.voys.nl/` en het blog daar, voor merkstem en woordkeuze.
- `https://www.voys.co/` als Engelstalige tegenhanger, alleen om te zien of de
  toon meebeweegt met de taal.

Neem minstens tien helpartikelen door, verspreid over verschillende categorieën
(aan de slag, veelvoorkomende problemen, beheer, apparatuur).

## Wat je moet opleveren

Een Nederlandstalig document `docs/research/voys-tone-of-voice.md` met:

1. **Aanspreekvorm en register.** Je/jij of u? Wij of Voys? Hoe formeel? Met
   letterlijke citaten als bewijs, met de URL erbij.
2. **Zinsbouw en lengte.** Korte zinnen of lange? Actief of passief? Geef
   gemeten indruk plus voorbeelden.
3. **Hoe ze een procedure opschrijven.** Genummerde stappen of proza? Hoe
   verwijzen ze naar knoppen en menu's? Nemen ze schermnamen letterlijk over?
4. **Vakjargon.** Welke termen gebruiken ze onvertaald (bijvoorbeeld rond
   telefonie), en welke leggen ze juist uit? Maak een lijstje van woorden die
   Voys wél gebruikt en van omschrijvingen die ze vermijden.
5. **Hoe ze slecht nieuws en beperkingen brengen.** Wat zeggen ze als iets niet
   kan, of als de klant iets zelf niet mag wijzigen?
6. **Humor, persoonlijkheid, emoji.** Zit dat erin, en waar wel en niet? Let op
   het verschil tussen marketingpagina's en helpartikelen — als dat verschilt,
   beschrijf beide en zeg welke van de twee de bot moet volgen.
7. **Een do/don't-tabel** van minstens twaalf regels, concreet genoeg om op te
   volgen. Niet "wees vriendelijk" maar bijvoorbeeld "schrijf 'je belplan' en
   niet 'uw belplan'".
8. **Een blok van maximaal 25 regels dat rechtstreeks in een systeemprompt kan.**
   Nederlands, imperatief geformuleerd, alleen regels die je in de bronnen
   daadwerkelijk hebt waargenomen.

## Regels voor dit onderzoek

- Elke bewering over de stijl onderbouw je met een citaat en een URL. Zonder
  bron niet opnemen.
- Verzin niets. Kun je iets niet vaststellen, schrijf dan dat je het niet hebt
  kunnen vaststellen.
- Sluit af met een sectie "Niet geverifieerd" waarin staat wat je niet hebt
  kunnen bekijken en waarom.
- Beschrijf geen merkrichtlijnen die je niet in de teksten terugziet; we willen
  de waargenomen stem, niet een gewenste.
```

## A.9 REQ-7 · brede modus met toestemming

```text
Bouw een brede modus met toestemming voor de helpdesk-widget. Werk in de
huidige werkmap. Dit is de subtielste opdracht van deze reeks; lees hem
helemaal voordat je begint.

## Het probleem

De helpdesk-bot antwoordt uitsluitend uit de helpartikelen. Vindt hij daar
niets, dan zegt hij dat en houdt op. Voor "hoe stel ik mijn belplan in" is dat
juist. Voor "wat is een SIP-trunk" is het zonde: dat kan hij prima uitleggen,
en de bezoeker escaleert nu onnodig.

De interne chat kent hiervoor een strict/open-schakelaar die de gebruiker
vooraf omzet. Dat model werkt hier niet: een bezoeker op een helppagina weet
niet wat "strict" betekent en gaat geen instellingen zoeken.

## Wat je bouwt

De bot biedt het zelf aan, op het moment dat het relevant is, en de bezoeker
zegt expliciet ja.

### Gedrag

1. **Aanbod.** Vindt de bot geen bruikbare bronnen in de helpartikelen, dan
   zegt hij dat (zoals nu) en biedt daarna aan om breder te kijken. Strekking:
   "Zal ik breder kijken op basis van algemene kennis? Let op: daar ben ik
   minder zeker over, en het is niet specifiek over ons." De precieze
   formulering volgt de tone of voice uit
   `docs/research/voys-tone-of-voice.md` — lees § 10 en § 11.
2. **Toestemming.** De bezoeker accepteert via een knop onder het bericht. Een
   expliciet "ja" in tekst mag de bot ook honoreren. Zonder toestemming
   gebeurt er niets.
3. **De schakelaar staat daarna aan voor dit gesprek**, met een zichtbare
   indicator in het chatvenster en een manier om hem weer uit te zetten. De
   bezoeker moet altijd kunnen zien in welke modus hij zit.
4. **Retrieval blijft altijd eerst.** Brede modus vervangt het zoeken in de
   helpartikelen niet; het is de terugval binnen dezelfde beurt wanneer die
   niets oplevert. Elke beurt zoekt dus gewoon eerst in de artikelen.
5. **Elk antwoord uit brede modus is zichtbaar gemarkeerd** als algemene
   kennis, duidelijk onderscheiden van een antwoord met bronnen uit de
   helpartikelen.

### De harde grens — dit is de kern van de opdracht

Brede modus betekent **algemene vakkennis**, niet "verzin maar iets over ons".

De bot mag in brede modus uitleggen wat DECT is, hoe nummerportering in
Nederland werkt, wat het verschil is tussen een SIP-trunk en een VoIP-account
in het algemeen.

De bot mag in brede modus NOOIT organisatie-specifieke feiten produceren:
geen prijzen, geen tarieven, geen functies of functienamen, geen instellingen,
geen beschikbaarheid, geen doorlooptijden, geen "bij ons doe je dat zo". Vraagt
de bezoeker daarnaar en staat het niet in de artikelen, dan blijft het antwoord
dat hij het niet weet — ook met brede modus aan.

Die scheiding is niet "zekerder versus minder zeker" maar "over de wereld
versus over ons". Formuleer die regel in de prompt zo scherp dat er geen
schemergebied is, en schrijf er tests voor.

### Meting — hier gaat het snel mis

6. **Een brede-modus-antwoord blijft een kennisgat.** De helpartikelen konden
   de vraag niet beantwoorden; dat is precies wat de gap-registratie moet
   vastleggen. Zorg dat het gap-event nog steeds gevuurd wordt
   (`app/services/partner_chat.py`, `_schedule_gap_event`), ook als de bot
   daarna breed antwoordt. Anders verdwijnt het redactionele signaal juist voor
   de onderwerpen waar content ontbreekt.
7. **Een brede-modus-antwoord telt niet als beantwoord uit de kennisbank.**
   Controleer hoe het uitkomstlabel (`app/services/widget_outcome.py`) en de
   citatie-compositie hierop reageren, en zorg dat een breed antwoord niet als
   bronbevestigd wordt geteld.

## Implementatie

- Promptkant: een variant of aanvulling op `SUPPORT_CHAT_SYSTEM_PROMPT` in
  `klai-libs/chat-prompts/klai_chat_prompts/__init__.py`. Laat GROUNDED,
  GENERAL, OPEN_KB en META letterlijk ongemoeid en controleer dat achteraf door
  de uiteindelijke waarden te vergelijken, niet de diff.
- Backend: `partner_chat.py` moet de modus per beurt kunnen ontvangen en het
  juiste profiel kiezen. Bestaand gedrag zonder de vlag blijft exact gelijk.
- Widget: knop onder het aanbod, modus-indicator, uitzetten mogelijk, en de
  modus meesturen bij volgende berichten. Bewaar de stand bij de rest van de
  gespreksstatus zodat hij een herlaadbeurt overleeft.
- Nederlandse en Engelse labels in de bestaande stijl.

## Randvoorwaarden

- Zonder toestemming verandert er niets aan het huidige gedrag. Dat is de
  belangrijkste regressie.
- Blijf binnen het bundelbudget (`npm run check-size`).
- Volg de bestaande codestijl per bestand.

## Gates — zelf draaien en de uitvoer tonen

- `klai-libs/chat-prompts`: bestaande tests plus je nieuwe.
- `klai-portal/backend`: `.venv/bin/python -m pytest tests/ -q` — gebruik de
  venv uit /Users/mvletter/conductor/workspaces/Klai/tegucigalpa/klai-portal/backend
  als er in deze worktree geen staat. Meld exact aantal geslaagd/gefaald.
- `klai-widget`: `npm ci` indien nodig, dan `npm run build` en
  `npm run check-size`.
```

## A.10 REQ-4 (vervolg) · afstemmen op de officiële merkstem

```text
Scherp de helpdesk-chatprompt aan op de officiële Voys-merkstem. Werk in de
huidige werkmap.

## Waar het over gaat

`klai-libs/chat-prompts/klai_chat_prompts/__init__.py` bevat
`SUPPORT_CHAT_SYSTEM_PROMPT`, de prompt voor de publieke helpdesk-widget. Die is
geschreven vóórdat we de officiële merkdocumentatie hadden. Die documentatie
staat nu, samen met een meting van de echte helppagina's en een vergelijking
tussen beide, in `docs/research/voys-tone-of-voice.md`. LEES DAT DOCUMENT
EERST — vooral § 10 (Nederlands specifiek) en § 11 (de vergelijking). De
wijzigingen hieronder komen daaruit voort.

## De vijf wijzigingen

1. **Verontschuldigen mag, precies twee keer zo vaak als nu (namelijk: soms).**
   De prompt kent nu geen regel hierover, en de gemeten helppagina's
   verontschuldigen zich nooit. Maar het merkdocument noemt "Onze excuses, we
   gaan dit oplossen" als vorm die werkt. § 11.2 legt uit waarom beide kloppen.
   Regel: één korte, gemeende verontschuldiging wanneer de bot zelf de fout in
   ging (verkeerd begrepen, onjuist antwoord) of wanneer de bezoeker een terecht
   ongenoegen uit. Nooit als vulmiddel, nooit meervoudig, nooit "helaas" als
   hele alinea, en niet bij een normale "dit staat niet in de artikelen".

2. **Niet alles weten is merkgedrag, geen tekortkoming.** Het merkdocument zegt:
   "it's okay not to have all the answers. What matters is caring enough to find
   a solution." De prompt behandelt het ontbrekende antwoord nu als een
   beperking. Herschrijf die passage zo dat toegeven dat je iets niet weet
   expliciet op de merkstem is, gekoppeld aan wat de bot dan wél doet. Dit
   versterkt de anti-hallucinatieregels; verzwak ze niet.

3. **Doorvragen is nieuwsgierigheid, geen rem.** De prompt staat nu maximaal
   één verduidelijkingsvraag toe, geformuleerd als beperking. Het merkdocument
   noemt "Curious — we ask questions, explore ideas" als karaktertrek. Zelfde
   gedrag — nog steeds maximaal één vraag, dan wachten — maar geformuleerd als
   iets wat de bot dóet omdat hij het antwoord goed wil hebben.

4. **De vriendtoets als slotregel.** Neem de toets uit het merkdocument
   letterlijk op: zou je dit tegen een vriend zeggen? Zet hem aan het eind, als
   laatste controle over de hele stijl.

5. **Concrete Nederlandse voorbeelden.** Neem uit § 10 een korte set
   vertaalvoorbeelden op die laten zien hoe een standaardzin in Voys-Nederlands
   klinkt ("Dit kan even duren", "Laat het gerust weten als je vastloopt",
   "Goed om te weten: ..."), en de lijst van wat níet werkt ("Geachte klant",
   "Wij verzoeken u vriendelijk", uitroeptekens-enthousiasme). Kies de meest
   sturende; maak er geen lange lijst van.

## Wat ongewijzigd blijft

- Alle bestaande regels rond bronvermelding, geen URLs of citatienummers
  schrijven, en de behandeling van meervoudige vragen.
- De escalatieregels: de bot kan niet doorverbinden, biedt wél een afspraak aan,
  en noemt NOOIT een telefoonnummer of e-mailadres — ook niet als het in een
  geciteerd artikel staat. Dit is een productbesluit, geen stijlkwestie.
- Het verbod op toezeggingen namens het bedrijf.
- GROUNDED, GENERAL, OPEN_KB en META blijven letterlijk gelijk. Controleer dat
  na afloop door de uiteindelijke waarden te vergelijken, niet de diff.
- De taaldetectie-preamble blijft ongewijzigd hergebruikt.

## Bewaak de lengte

De prompt wordt hierdoor langer. Houd hem strak: schrap waar de nieuwe regels
bestaande formuleringen overbodig maken, in plaats van er alleen bij te
schrijven. Meld in je rapport hoeveel tekens de prompt vóór en na je wijziging
telt.

## Gates — zelf draaien en de uitvoer tonen

- `klai-libs/chat-prompts`: de bestaande tests, plus een test die aantoont dat
  de vier andere profielen ongewijzigd zijn.
- `klai-portal/backend`: `.venv/bin/python -m pytest tests/ -q`, met het exacte
  aantal geslaagd/gefaald.
```
