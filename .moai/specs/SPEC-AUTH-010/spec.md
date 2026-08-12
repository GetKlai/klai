---
id: SPEC-AUTH-010
version: "0.2.0"
status: draft
created: "2026-08-12"
updated: "2026-08-12"
author: MoAI
priority: P1
supersedes: null
related: [SPEC-AUTH-006, SPEC-AUTH-008, SPEC-AUTH-009]
---

## HISTORY

| Date       | Version | Change                                                              |
|------------|---------|---------------------------------------------------------------------|
| 2026-08-12 | 0.1.0   | Initial draft                                                       |
| 2026-08-12 | 0.2.0   | R4 uitgewerkt (two-phase password signup), R6 latente bugs, R7 login-matrix, R8 picker-labels |

# SPEC-AUTH-010: Domain-match join in de signup-flows

## Context

SPEC-AUTH-009 bouwde het Slack/Notion-model: `portal_orgs.primary_domain` (founder-implicit
domain claim), de `auto_accept_same_domain` toggle, de `/select-workspace` picker en de
join-request pipeline. De 4-case domain-match matrix draait echter **alleen** in
`idp_callback` (SSO-login voor bestaande Zitadel-users). De plekken waar een nieuwe
collega daadwerkelijk binnenkomt, checken het domein niet:

1. **`POST /api/signup`** (e-mail + wachtwoord) — maakt altijd een nieuwe org aan
   (signup.py:221), vóór e-mailverificatie. Collega nr. 2 van voys.nl sticht workspace
   nr. 2 of strandt op een 409.
2. **`POST /api/signup/social`** — maakt altijd een nieuwe org aan (signup.py:506) en
   claimt onvoorwaardelijk `primary_domain` (signup.py:577).
3. **`GET /auth/idp-callback` met een gloednieuwe IdP-gebruiker** — bereikt de matrix
   nooit: `create_session_with_idp_intent` raist `ValueError` ("no userId linked",
   zitadel.py:1016), de bare `except` (auth.py:2165) 302't zonder foutmelding terug naar
   `/login`. Stille dead-end; onbetest.
4. **`POST /auth/login`** (wachtwoord) — draait de matrix niet; een gebruiker zonder
   portal_users rij finaliset gewoon en strandt op `/no-account`.

Daarnaast bevatten de bestaande join-paden drie latente productiebugs (R6) die elk pad
dat wij aanzetten direct zouden breken.

Er komt **geen** nieuwe join-mechaniek bij: alle joins lopen via de bestaande paden
(auto-join INSERT of join-request, incl. bestaande notificaties en admin-goedkeuring).

**Enumeratie-standpunt:** workspace-namen worden pas getoond nadat het e-mailadres
geverifieerd is (IdP-verified of validatielink geklikt). Vóór verificatie lekt de flow
hoogstens het boolean feit dat er "al een workspace voor dit domein" bestaat —
gelijkwaardig aan Notion, en beschermd door de bestaande signup-rate-limits.

## Out of scope

- DNS-domeinverificatie, multi-domain per workspace, domain-takeover.
- Picker voor bestaande members bij wachtwoord-login (member ≥1 blijft direct
  finaliseren; alleen het 0-member pad krijgt de matrix). Bewuste scope-cut: geen
  UX-verandering voor bestaande gebruikers.
- `klai_sso` cookie in de picker-finalize paden (bestaande gap, geldt ook voor IdP-flow;
  aparte fix indien nodig).
- Seat-/billinglimieten bij auto-join (AUTH-009 gedrag ongewijzigd).
- `/no-account` first_name/last_name param-mismatch (cosmetisch).

## Requirements

### R1: Social signup — domain-match branch vóór org-creatie

**WHEN** `POST /api/signup/social` wordt aangeroepen en
`primary_domain_for_email_domain(idp_email_domain)` niet leeg is en er ≥1 niet-verwijderde
`portal_orgs` met dat `primary_domain` bestaat en de request niet expliciet
`create_new_workspace=true` meegeeft,
**THEN** SHALL het endpoint géén org aanmaken maar een discriminated response
`{kind: "domain_match", orgs: [{org_id, name, auto_accept}]}` teruggeven (HTTP 200).

- C1.1: E-mail is op dit punt IdP-verified (zitadel.py:976 `isVerified: True`); namen
  tonen is toegestaan.
- C1.2: Free-email domeinen volgen ongewijzigd het bestaande pad. Bestaande test
  `test_free_email_social_signup_creates_workspace_without_claiming_domain` blijft groen.
- C1.3: `create_new_workspace=true` doorloopt het bestaande org-creatie pad ongewijzigd.
- C1.4: De frontend (`/$locale/signup/social`) toont bij `kind=domain_match` de
  workspace(s) met per workspace CTA "Word lid" (auto_accept=true) of "Vraag toegang aan",
  plus een ondergeschikte link "toch een eigen werkruimte starten"
  (herhaalt submit met `create_new_workspace=true`).
- C1.5: De `klai_idp_pending` cookie wordt in de domain_match-response NIET geconsumeerd
  (de gebruiker moet erna nog kunnen joinen of alsnog creëren binnen de cookie-TTL).

### R2: Social signup — join uitvoeren

**WHEN** de gebruiker in de social-signup flow een domain-match workspace kiest,
**THEN** SHALL `POST /api/signup/social/join` (nieuw; zelfde `klai_idp_pending`
cookie-binding en `_verify_idp_pending_binding` als `/signup/social`):
- bij `auto_accept_same_domain=true`: idempotent een `portal_users` rij inserten
  (`role='personal'`, `status='active'`, seat via `suggest_seat("personal")`), admins
  notificeren via het bestaande auto-join template, en de gebruiker ingelogd krijgen
  op dezelfde manier als het bestaande existing-user pad in `idp_signup_callback`
  (klai_sso cookie + redirect), response `{kind: "auto_join", redirect_url}`;
- bij `auto_accept_same_domain=false`: een `portal_join_requests` rij aanmaken met
  `org_id` gezet, HMAC approval token, admins notificeren via het bestaande
  join-request-template, géén cookie, response
  `{kind: "join_request_pending", redirect_to: "/join-request/sent"}`.

- C2.1: `org_id` wordt server-side gevalideerd tegen een verse domain-match query —
  nooit client-vertrouwen. Mismatch → 403.
- C2.2: `set_tenant(db, org_id)` vóór elke INSERT (portal_users én portal_join_requests;
  RLS WITH CHECK vereist dit).
- C2.3: Ontbrekende/verlopen/tampered cookie → 400/403 conform bestaand gedrag.
- C2.4: Idempotentie: bestaande portal_users rij (race) → behandel als member, geen 500.

### R3: idp_callback — nieuwe IdP-gebruiker bereikt de matrix

**WHEN** `GET /auth/idp-callback` een IdP-intent ontvangt zonder gelinkte Zitadel-user,
**THEN** SHALL het endpoint de gebruiker provisionen via het bestaande
`create_zitadel_user_from_idp` + `create_session_for_user_idp` pad (gedeelde logica met
`idp_signup_callback`, incl. de bestaande CQRS-404-retry) en daarna de bestaande 4-case
matrix normaal doorlopen.

- C3.1: Resultaat gloednieuwe Google-gebruiker: domain-match → picker; geen match →
  `/no-account` → social signup. De stille 302 naar `/login` verdwijnt.
- C3.2: Bestaand gedrag voor al-gelinkte users blijft gelijk (`test_r3_idp_callback.py`
  blijft groen, eventueel met aangepaste mock-surface).
- C3.3: Faalt user- of sessie-creatie alsnog → bestaande failure_url flow.
- C3.4: Omdat de intent maar één keer opgehaald kan worden, haalt idp_callback de intent
  zelf éénmalig op en hergebruikt de data voor zowel user-creatie als sessie-creatie.

### R4: Password signup — two-phase domain-match

Fase 1 (choice, geen side effects):
**WHEN** `POST /api/signup` binnenkomt zonder `domain_choice` veld, zonder geldig
invite-token, met een niet-free e-maildomein dat matcht met ≥1 bestaande workspace,
**THEN** SHALL het endpoint vóór enige Zitadel/DB-write stoppen en
`{kind: "domain_match", domain: "voys.nl"}` teruggeven (HTTP 200, géén workspace-namen
— e-mail is nog onbewezen).

Fase 2 (uitvoering):
- `domain_choice="join"`: SHALL de Zitadel human user aanmaken met verificatiemail
  (bestaand pad, portal-org) maar GÉÉN tenant-org en GÉÉN portal_users rij. Response
  `{kind: "join_pending"}`; UI toont "check je mail; daarna kun je je aansluiten".
- `domain_choice="create"` (of geen match): bestaand pad ongewijzigd (org + user + mail).

- C4.1: Fase 1 lekt alleen het domein-boolean, geen namen (enumeratie). Bestaande
  rate-limit (`check_signup_email_rate_limit`) geldt óók voor fase-1 calls.
- C4.2: Free-email + invite-token paden ongewijzigd (invite-token impliceert
  `domain_choice="create"`-gedrag zoals vandaag).
- C4.3: De join zelf gebeurt pas ná e-mailverificatie, bij eerste login (R7).

### R5: Auto-accept keuze bij workspace-creatie

**WHEN** een oprichter een nieuwe workspace aanmaakt (password of social signup) met een
zakelijk domein,
**THEN** SHALL de signup-UI een checkbox tonen "Collega's met een @{domain}-adres mogen
automatisch meedoen" (default: aangevinkt) en SHALL het backend-endpoint
`auto_accept_same_domain` uit de body overnemen.

- C5.1: Server-side guard: `auto_accept_same_domain=true` wordt alleen gehonoreerd als
  `primary_domain` niet leeg is (free-email → altijd false).
- C5.2: De bestaande admin-toggle blijft leidend na creatie.

### R6: Repareer latente bugs in de bestaande join-paden

- R6.1: `_handle_auto_join` (auth_select.py:318) insert `role="member"` — geen geldig
  enum-label. SHALL `role="personal"` worden (RBAC-REFACTOR-001 REQ-11) + expliciete
  `seat_type=str(suggest_seat("personal"))`. Bijbehorende test-assertion aanpassen.
- R6.2: `notify_auto_join_admins` (auth_select.py:117) en `_handle_join_request`
  (auth_select.py:406) filteren op `role.in_(["admin","group-admin"])` — `"group-admin"`
  is geen enum-label en aborteert de transactie op live Postgres. SHALL
  `["admin","group_manager"]` worden.
- R6.3: `approve_join_request` via `?token=` (admin/join_requests.py) doet de
  status-UPDATE en portal_users INSERT zonder `set_tenant` — RLS WITH CHECK faalt.
  SHALL `set_tenant(db, jr.org_id)` aanroepen zodra de org bekend is.

### R7: Password login — matrix voor gebruikers zonder workspace

**WHEN** `POST /auth/login` (of `POST /auth/totp-login`) succesvol authenticeert en de
gebruiker heeft **nul** portal_users rijen en het e-mailadres is geverifieerd en er is
≥1 domain-match workspace,
**THEN** SHALL het endpoint NIET direct finaliseren maar een pending-session opslaan
(entries met kind=domain_match, zoals idp_callback) en
`{status: "select_workspace", ref}` teruggeven; de frontend navigeert naar
`/select-workspace?ref=…`. Picker-join werkt daarna via het bestaande
`POST /auth/select-workspace` (incl. R6-fixes).

- C7.1: Members ≥1 → bestaand gedrag (direct finaliseren), expliciete scope-cut.
- C7.2: 0 members + geen match → bestaand gedrag (finalize → `/no-account`).
- C7.3: E-mail niet geverifieerd → bestaand gedrag (geen join-aanbod).
- C7.4: Redis/pending-session failure → bestaand gedrag (finalize), zoals de guard in
  idp_callback; nooit een harde fout op het login-pad.
- C7.5: Zelfde branch in `totp_login` (na geslaagde TOTP), via gedeelde helper.

### R8: Picker toont join-semantiek

**WHEN** `GET /api/auth/pending-session` entries met `kind="domain_match"` bevat,
**THEN** SHALL de response per org ook `kind` en `auto_accept` bevatten en SHALL de
picker-UI de CTA's onderscheiden: "Word lid" (auto_accept) vs "Vraag toegang aan",
conform AUTH-009 C3.2. Member-entries blijven renderen zoals vandaag.

## Review-addenda (na adversariële review + tenant-review, 2026-08-12)

- C2.5 (M2): `POST /signup/social/join` is idempotent — bestaande pending
  request per (user, org) → zelfde response, geen nieuwe rij, geen nieuwe
  adminmail. Auto-join notificeert alleen bij een daadwerkelijk nieuwe join.
  `/api/signup/*` valt onder de Caddy sensitive rate-limit zone.
- C4.4 (M1): `domain_choice="join"` terwijl de match verdween tussen fase 1
  en 2 → HTTP 409, nooit stilzwijgend een workspace creëren.
- C3.5 (H1): de gedeelde sessie-retry-helper vangt ook netwerkfouten
  (httpx.RequestError) — een transient probleem 302't naar de failure_url,
  nooit een kale 500 midden in de OIDC-redirect.
- C7.6 (L3): `PendingSessionService.store` faalt luid zonder Redis, zodat de
  degrade-paden van de callers (finalize / terug naar login) pakken.
- Afwijking van R1/R4: de domain_match/join_pending responses dragen HTTP 201
  (route-level status van de bestaande signup-endpoints); de frontend
  discrimineert op `kind`, niet op statuscode. Bewust geaccepteerd om de
  bestaande response-contracten niet te breken.

## Acceptance (samenvatting)

- AC-1: Social signup met matchend domein → geen nieuwe org; join-keuze; join (auto) →
  direct lid + ingelogd + adminnotificatie; join (verzoek) → pending request +
  notificatie, `/join-request/sent`.
- AC-2: `create_new_workspace=true` → bestaand gedrag.
- AC-3: Nieuwe Google-gebruiker via `/login` → picker of `/no-account`, nooit stille
  bounce.
- AC-4: Password signup met matchend domein → choice → join_pending (geen org) → na
  verificatie + login → picker → lid of verzoek.
- AC-5: Checkbox bij creatie stuurt `auto_accept_same_domain` (server-side guarded).
- AC-6: R6-bugs gerepareerd met regressietests.
- AC-7: Alle bestaande auth-tests groen (met bijgewerkte mock-surface waar de SPEC dat
  expliciet zegt).
