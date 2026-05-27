# Implementation Plan — SPEC-HUBSPOT-DRAFT-REPLY-001

**SPEC:** SPEC-HUBSPOT-DRAFT-REPLY-001 — HubSpot Help Desk draft reply via Klai knowledgebase
**Status:** draft
**Author:** Mark Vletter
**Created:** 2026-05-27

---

## Goal

Bouw een HubSpot Help Desk app card waarmee een supportmedewerker vanuit een ticket op **Draft concept reply** kan klikken. Klai haalt de relevante HubSpot conversation/context op, genereert een antwoord uit de knowledgebase, toont een preview met bronnen, en opent daarna HubSpot's native one-to-one email composer met `initialEmailSubject` en `initialEmailBody` gevuld.

De v1 is bewust click-based en human-in-the-loop. Er komt nog geen automatische verzending en geen pre-generation via webhook.

---

## Key Findings

- HubSpot UI extensions ondersteunen app cards op `helpdesk.sidebar` voor `tickets`.
- HubSpot `CrmActionButton` ondersteunt `actionType="SEND_EMAIL"` met `initialEmailSubject` en `initialEmailBody`.
- HubSpot biedt geen publieke API om de bestaande Help Desk reply composer direct te vullen of een native reply-draft in dezelfde thread aan te maken.
- HubSpot's documented app-card locations do not include the Conversations Inbox (`/live-messages/.../inbox`). Inbox placement is therefore not available through official UI extensions for v1.
- Klai heeft al een bruikbare RAG/LLM-route in `klai-portal/backend/app/services/partner_chat.py` en `klai-portal/backend/app/api/partner.py`.
- Voor v1 is een HubSpot app card + Klai backend wrapper de kortste betrouwbare route.

---

## Rollout Context

- Live HubSpot portal: `https://app-eu1.hubspot.com/reports-dashboard/5604529/`
- Eerste testomgeving: HubSpot sandbox, niet live.
- HubSpot sandbox portal/account: `Sandbox environment | Voys` — Hub ID `147785398`.
- Eerste Klai tenant: `voys`.
- Eerste knowledgebase: Voys support KB.
- Eerste rollout-type: internal POC met handmatige klikflow, geen webhook en geen auto-send.
- Local scaffold status: `klai-hubspot/klai-email-support/` created with a valid HubSpot project and a first `helpdesk.sidebar` card POC.
- HubSpot CLI sandbox account: `sandbox-voys` -> Hub ID `147785398`.
- HubSpot project profile: `sandbox` -> target account `147785398`.
- Sandbox deploy status: build #1 deployed successfully.
- Sandbox deploy URL: `https://app.hubspot.com/developer-projects/147785398/project/klai-email-support/activity/deploy/1`
- Required HubSpot UI step after deploy: install the app from the project page (`App -> Distribution -> Install now`) before it appears in Help Desk card library/location management.
- Help Desk layout status: `Klai Email Support` card added to the sandbox default Help Desk sidebar and confirmed rendering with portal `147785398` and ticket context.
- Build #2 status: code build succeeded, deploy blocked by missing HubSpot secret `KLAI_PARTNER_API_KEY`.
- Build #3 status: deployed successfully to sandbox `147785398` after re-authenticating the HubSpot CLI and adding HubSpot secret `KLAI_PARTNER_API_KEY`.
- Current implementation slice: HubSpot app card calls private serverless function with `ticketId`, `portalId`, and ticket properties `subject` + `content`; function resolves the associated contact, calls Klai Partner API `POST https://api.getklai.com/partner/v1/chat/completions` with `stream=false`, and returns preview/body/sources for `SEND_EMAIL`.
- Current blocker resolved: HubSpot CLI account `sandbox-voys` now has enough scopes for project secret management.
- API design update: keep the Partner API OpenAI-compatible and add an optional Klai `knowledge` extension on `/partner/v1/chat/completions`. `messages` remain the generation prompt/data; `knowledge.query` is the clean retrieval query; `knowledge.knowledge_base_ids` scopes KB access; `knowledge.top_k` controls retrieved context size. This keeps the endpoint system-agnostic and avoids HubSpot-specific draft endpoints.
- Implementation update: backend now supports `knowledge.enabled`, `knowledge.query`, `knowledge.knowledge_base_ids`, `knowledge.top_k`, and `knowledge.include_sources`. HubSpot POC now sends the customer/ticket data in `messages` and a separate clean `knowledge.query` against support KB id `42` with `top_k=20`.

De live portal blijft alleen referentie totdat de sandbox POC bewezen is. Alle HubSpot app-installatie, tokenconfiguratie en card-tests moeten eerst in sandbox gebeuren.

---

## Architecture

```
HubSpot Help Desk ticket
  |
  v
Klai app card in helpdesk.sidebar
  |
  |  click: Draft concept reply
  v
HubSpot serverless function or Klai backend call
  |
  v
Klai portal-api /api/integrations/hubspot/draft-reply
  |
  +--> HubSpot API: find thread by associatedTicketId
  +--> HubSpot API: fetch messages + contact
  +--> Klai retrieval + LiteLLM synthesis
  |
  v
App card preview: body + sources + confidence
  |
  |  click: Open e-mailconcept
  v
HubSpot CrmActionButton SEND_EMAIL
  |
  v
HubSpot native email composer with subject/body prefilled
```

---

## Scope

### In Scope

- HubSpot private app/project for the first customer-owned POC.
- App card in `helpdesk.sidebar`, scoped to `tickets`.
- Manual button: **Draft concept reply**.
- Klai backend endpoint that generates a support-mail draft from HubSpot ticket/conversation context.
- Preview in the card before opening the email composer.
- HubSpot `SEND_EMAIL` action with generated subject/body.
- Basic audit logging for draft generation.
- Unit tests for HubSpot context extraction and prompt construction.

### Out of Scope

- Auto-send replies.
- Webhook-based pre-generation.
- Native insertion into the active Help Desk reply composer.
- Native placement inside the HubSpot Conversations Inbox UI.
- Marketplace/public HubSpot app review.
- Multi-portal OAuth install flow beyond what is needed for the POC.
- Full admin UI for mapping HubSpot portals to Klai orgs.

---

## Dependency Graph

```
Fase A — HubSpot POC project + scopes
  |
  v
Fase B — Klai HubSpot backend service
  |
  v
Fase C — Draft generation service using existing Klai RAG
  |
  v
Fase D — HubSpot app card UI
  |
  v
Fase E — End-to-end HubSpot test
  |
  v
Fase F — Production hardening
```

Fase B en C kunnen deels parallel, maar de first working demo moet B eerst hebben: zonder HubSpot ticket/contact/thread context kan de draft prompt niet realistisch worden getest.

---

## Milestones

### Priority High — Fase A: HubSpot POC Setup

**Deliverable:** een werkende private HubSpot app card skeleton in Help Desk.

**Tasks:**

1. Maak een HubSpot developer/private app project.
2. Configureer een card:
   - `location: "helpdesk.sidebar"`
   - `objectTypes: ["tickets"]`
   - naam: `Klai`
3. Voeg scopes toe:
   - `tickets`
   - `crm.objects.contacts.read`
   - `conversations.read`
4. Maak een minimale card met:
   - huidige `ticketId` uit HubSpot context
   - knop **Draft concept reply**
   - placeholder preview state
5. Verifieer in HubSpot Help Desk dat de card zichtbaar is op ticket views.

**Acceptance:**

- De card verschijnt op een HubSpot Help Desk ticket.
- De card kan het huidige ticket-ID doorgeven aan een serverless function of externe backend call.

---

### Priority High — Fase B: Klai HubSpot Backend Service

**Deliverable:** backend service die HubSpot ticket/context kan ophalen.

**Files:**

- `klai-portal/backend/app/api/hubspot.py` [NEW]
- `klai-portal/backend/app/services/hubspot_client.py` [NEW]
- `klai-portal/backend/app/services/hubspot_draft_reply.py` [NEW]
- `klai-portal/backend/tests/test_hubspot_draft_reply.py` [NEW]

**Endpoint:**

```http
POST /api/integrations/hubspot/draft-reply
```

**Request:**

```json
{
  "portalId": "123456",
  "ticketId": "987654",
  "hubspotUserId": "optional"
}
```

**Response:**

```json
{
  "contactId": "123",
  "subject": "Re: vraag over ...",
  "body": "Hoi ...",
  "sources": [
    {
      "title": "Handleiding ...",
      "url": "https://..."
    }
  ],
  "confidence": 0.86,
  "warnings": []
}
```

**Tasks:**

1. Voeg HubSpot config toe aan `app/core/config.py`:
   - `hubspot_access_token` for POC
   - later: encrypted OAuth token lookup
2. Bouw `HubSpotClient` met methods:
   - `get_ticket(ticket_id)`
   - `find_threads_for_ticket(ticket_id)`
   - `get_thread(thread_id)`
   - `get_thread_messages(thread_id)`
   - `get_contact(contact_id)`
3. Filter messages:
   - laatste inkomende klantmail als primaire query
   - eerdere klant/agent turns als conversation history
   - strip quoted email history waar mogelijk
4. Bouw een compacte `HubSpotSupportContext` dat geen ruwe overbodige HubSpot payloads doorgeeft aan de LLM.
5. Voeg foutafhandeling toe:
   - geen thread gevonden -> 404 met actionable detail
   - geen contact gevonden -> 422
   - HubSpot timeout -> 502
   - ontbrekende token/config -> 503

**Acceptance:**

- Met een geldig `ticketId` retourneert de service contact, thread en laatste klantvraag.
- HubSpot API fouten lekken geen tokens of volledige customer payloads naar logs.

---

### Priority High — Fase C: Draft Generation via Existing Klai RAG

**Deliverable:** support-mail draft generator bovenop bestaande retrieval + LiteLLM.

**Tasks:**

1. Hergebruik bestaande primitives uit `app.services.partner_chat`:
   - retrieval context
   - non-streaming LiteLLM completion
   - citation/source handling
2. Maak een aparte support-mail prompt:
   - antwoord in de taal van de klant
   - kort, vriendelijk, concreet
   - alleen feiten uit knowledgebase/context
   - geen zichtbare bronlinks in de klantmail zelf
   - bij twijfel: geef een voorzichtig antwoord of vraag om menselijke controle
3. Bouw messages:
   - system: support-mail drafting rules
   - user: laatste klantmail
   - assistant/user history: relevante thread turns
4. Return:
   - `body`: klantklare mailtekst
   - `sources`: bronlijst voor de app card
   - `confidence`: eerste versie heuristisch op basis van retrieval strength/source count
5. Log product event:
   - `hubspot.draft_reply.generated`
   - org_id, portalId, ticketId, source_count, confidence bucket

**Acceptance:**

- Draft blijft in dezelfde taal als de klantvraag.
- Draft hallucineert geen unsupported details bij lege of zwakke retrieval.
- Preview bevat bronnen voor de medewerker, maar de mail body niet.

---

### Priority High — Fase D: HubSpot App Card UI

**Deliverable:** card waarmee een medewerker draft genereert en HubSpot composer opent.

**UI States:**

- idle: knop **Draft concept reply**
- loading: disabled knop + spinner/tekst
- success: preview + source list + **Open e-mailconcept**
- error: compacte foutmelding + retry

**Tasks:**

1. Implementeer card frontend met `@hubspot/ui-extensions`.
2. Op klik:
   - call `hubspot.serverless(...)` of direct Klai backend endpoint
   - stuur `ticketId`, `portalId`, HubSpot context waar beschikbaar
3. Toon preview:
   - subject
   - body
   - bronnen
   - confidence/warnings
4. Render `CrmActionButton`:

   ```jsx
   <CrmActionButton
     actionType="SEND_EMAIL"
     actionContext={{
       objectTypeId: "0-1",
       objectId: contactId,
       initialEmailSubject: subject,
       initialEmailBody: body,
     }}
   >
     Open e-mailconcept
   </CrmActionButton>
   ```

5. Voeg `copyTextToClipboard` fallback toe als `SEND_EMAIL` in een specifieke HubSpot context niet werkt.

**Acceptance:**

- Medewerker klikt op **Draft concept reply** en ziet binnen redelijke tijd een preview.
- **Open e-mailconcept** opent HubSpot composer met juiste contact, subject en body.
- De medewerker kan de tekst bewerken voordat hij/zij verzendt.

---

### Priority Medium — Fase E: End-to-End Validation

**Deliverable:** geteste POC in echte HubSpot Help Desk omgeving.

**Test Matrix:**

1. Ticket met een eenvoudige FAQ-vraag.
2. Ticket met meerdere eerdere klant/agent turns.
3. Ticket zonder gekoppeld contact.
4. Ticket zonder associated conversation thread.
5. Nederlandse klantmail.
6. Engelse klantmail.
7. Zwakke retrieval/no answer.
8. HubSpot API timeout.

**Acceptance:**

- Happy path opent een bruikbaar e-mailconcept.
- Edge cases tonen een duidelijke fout of waarschuwing in de card.
- Geen automatische mail wordt verzonden.

---

### Priority Medium — Fase F: Production Hardening

**Deliverable:** veilig genoeg om bij klanten uit te rollen.

**Tasks:**

1. Vervang POC-token door OAuth install/token storage.
2. Sla HubSpot tokens encrypted op met bestaande connector credential patterns.
3. Voeg tenant mapping toe:
   - HubSpot `portalId` -> Klai `org_id`
   - eventueel default `knowledge_base_ids`
4. Rate limit draft generation per org/user/ticket.
5. Idempotency/cache:
   - korte cache op `ticketId + latestMessageId`
   - voorkom meerdere dure generations bij dubbelklikken
6. Observability:
   - latency
   - HubSpot API error rate
   - draft generated count
   - composer-open count where measurable
7. Security review:
   - token masking
   - customer email PII handling
   - prompt injection guardrails
   - no raw thread dumps in logs

**Acceptance:**

- HubSpot credentials zijn encrypted en rotatable.
- PII wordt minimaal gelogd.
- Draft generation is bounded by rate limits and timeouts.

---

## API Notes

### HubSpot APIs

- Conversations threads can be filtered by `associatedTicketId`.
- Thread payloads can include `associatedContactId` and ticket associations.
- `conversations.read` is sufficient for retrieving conversation data.
- `conversations.write` is not required for v1, because v1 does not send messages via API.

### HubSpot UI Extension Constraints

- `helpdesk.sidebar` app cards are supported for tickets.
- `CrmActionButton SEND_EMAIL` can prefill subject/body.
- The action opens a one-to-one email composer, not necessarily the active Help Desk thread reply editor.
- A clipboard fallback should remain available for HubSpot context limitations.

---

## Risks

### Risk: Email not logged against the ticket

HubSpot may log the sent one-to-one email against the contact but not the original ticket/conversation.

**Mitigation:** test from ticket context first. If needed, pass ticket object context to `SEND_EMAIL` or log/link the resulting engagement in a follow-up phase.

### Risk: No direct native reply-thread draft

HubSpot does not expose a public draft composer API for Help Desk replies.

**Mitigation:** position v1 as "open filled HubSpot email composer", not "insert into current reply box".

### Risk: Prompt injection via customer email

Customer mails can include adversarial instructions.

**Mitigation:** support-mail prompt must explicitly treat customer email as untrusted input and only answer from retrieved knowledgebase/context.

### Risk: Wrong Klai knowledgebase selection

HubSpot ticket alone may not identify which Klai KB to use.

**Mitigation:** start with org-level default KB mapping; add admin configuration later.

---

## Open Questions

1. Which HubSpot account/portal is the first POC target?
2. Should one HubSpot portal map to exactly one Klai org, or can one portal route to multiple Klai knowledgebases?
3. Should the card show sources to the support agent by default, or hide them behind an expand action?
4. Should the generated body include a greeting/signature, or should HubSpot templates/signatures handle that?
5. Is the target workflow Help Desk tickets only, or also CRM contact/company records later?

---

## First Implementation Slice

Build the smallest demo that proves the interaction:

1. Install the private HubSpot app in the HubSpot sandbox.
2. Make the HubSpot card visible on `helpdesk.sidebar`.
3. Card sends the sandbox `ticketId` to Klai.
4. Klai maps the HubSpot sandbox portal to the `voys` tenant and Voys support KB.
5. Klai fetches thread + latest inbound message.
6. Klai generates a draft using existing retrieval + LiteLLM.
7. Card shows preview.
8. `SEND_EMAIL` opens composer with generated subject/body.

Do not build OAuth, webhooks, auto-send, or admin configuration until this slice is verified in a real HubSpot ticket.
