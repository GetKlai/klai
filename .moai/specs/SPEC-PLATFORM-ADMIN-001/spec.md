# SPEC-PLATFORM-ADMIN-001 — Platform-admin console (cross-tenant user/org/bot beheer)

Status: in progress
Author: Klai
Created: 2026-05-22

## 1. Probleem

Klai-staff (Jantine, Mark, Steven) heeft geen UI om over alle tenants
heen te kijken. `/admin/users` is strikt org-gescoped
(`PortalUser.org_id == perms.org_id`). Om "alle klanten / alle users"
te zien moet je nu directe SQL op productie of Grafana gebruiken. Dat
is traag, foutgevoelig en niet auditbaar.

We willen een **platform-admin console** — één scherm met cross-tenant
overzicht van users, organisaties, bots (widgets/agents) en
abonnementen, plus een paar topline-metrics. Model: het TalkWithData
Admin Panel.

## 2. Doelgroep + autorisatie

[HARD] Uitsluitend bereikbaar voor `is_platform_admin` — d.w.z. een
caller wiens org-slug gelijk is aan `settings.platform_org_slug`
(default `getklai`). Geen enkele tenant-admin (gewone klant) mag deze
endpoints of pagina zien.

[HARD] Elke cross-org read draait via `cross_org_session()` (RLS-bypass)
EN logt een audit-event (`platform_admin.viewed`) met de caller,
het tabblad en eventuele zoekterm. Geen stille cross-tenant reads.

[HARD] De `cross_org_session()` mag NOOIT bereikbaar zijn vanuit een
endpoint dat niet eerst `require_platform_admin()` heeft gepasseerd.

## 3. Surfaces

### Topline stat-cards (4)

| Card | Bron |
|---|---|
| Total users | `COUNT(*) FROM portal_users WHERE status != 'offboarded'` + delta deze maand |
| Active subscriptions | `COUNT(*) FROM portal_orgs WHERE deleted_at IS NULL AND billing_status IN ('active','trialing')` (+ aantal orgs) |
| Total bots | `COUNT(*) FROM widgets` + delta vandaag |
| MRR | Som per org `plan→prijs × seats` (v1: prijstabel stub → mag €0 tonen tot pricing-config bestaat) + ARR = MRR×12 |

### Tabs

1. **Users** — alle users over alle orgs. Kolommen: naam+email (+Admin
   badge), Organisatie (+"Niet onboarded" badge bij
   provisioning_status != complete), Plan, Aangemaakt, Laatste login,
   Acties (Bekijk / Bewerk → diept in de org). Zoek op naam/email.
2. **Organisaties** — alle tenants. Kolommen: naam, slug, plan,
   billing_status, # users, # bots, aangemaakt, provisioning-status.
3. **Abonnementen** — per org: plan, billing_cycle, seats,
   billing_status, moneybird-koppeling ja/nee.
4. **Bots** — alle widgets/agents over alle orgs. Kolommen: naam, org,
   # kennisbanken, aangemaakt.
5. **Chat errors** — recente fout-events (v1: stub/laatste N uit
   `product_events` of leeg; volledige integratie = follow-up).

## 4. Endpoints (allemaal onder `/api/admin/platform/`)

Alle gegate op `require_platform_admin()`, alle cross_org_session,
alle audit-logged.

```
GET /api/admin/platform/stats
GET /api/admin/platform/users?search=&limit=&offset=
GET /api/admin/platform/organizations?search=
GET /api/admin/platform/bots?search=
GET /api/admin/platform/chat-errors?limit=
```

Response-modellen: zie `app/api/admin/platform.py`. Pydantic, mirror
1:1 naar frontend types in `routes/admin/platform/-types.ts`.

## 5. Frontend

Route `/admin/platform` (platform-admin gated via `is_platform_admin`
op `/api/me`). Sidebar-entry alleen zichtbaar voor platform-admins.

Layout = TWD admin panel:
- Titel "Platform" + subtitle
- 4 stat-cards
- Tab-bar (Users / Organisaties / Abonnementen / Bots / Chat errors)
- Zoekbalk + Refresh
- Tabel per tab, canonieke dashboard-row stijl (klai-hover, klikbaar
  waar een detail/diepte bestaat)

## 6. Acceptatiecriteria

- [x] AC1: Een gewone tenant-admin krijgt 403 op elke
      `/api/admin/platform/*` endpoint. (require_platform_admin op elk
      endpoint; ongeauth → 401, niet-platform-admin → 403.)
- [x] AC2: Een platform-admin ziet users/orgs/bots van MEERDERE
      tenants in één lijst. (cross_org_session reads; geverifieerd:
      20 users / 7 orgs / 4 bots cross-tenant.)
- [x] AC3: Stat-cards tellen cross-tenant correct. (stats-SQL tegen
      prod: users/orgs/subs/bots/KBs/docs.)
- [x] AC4: Zoeken op naam/email filtert cross-tenant. (ILIKE op
      email/display_name/org-naam.)
- [x] AC5: Elke platform-read + write schrijft een audit-event.
      (platform_admin.viewed + .user_role_changed / _suspended /
      _reactivated / _invited.)
- [x] AC6: Sidebar-entry "Platform" verschijnt alleen bij
      is_platform_admin. (platformAdminOnly filter in admin/route.tsx.)
- [x] AC7: Geen RLS-regressie — reads via cross_org_session achter de
      gate; writes via tenant_scoped_session(target_org) zodat RLS de
      write naar exact één tenant afdwingt.

## 8. Geleverd (fase A/B/C)

- Fase A: KB + document counts cross-tenant (stats, org-lijst,
  org-detail). knowledge.artifacts join op zitadel_org_id.
- Fase B: cross-tenant rol-wijziging + suspend/reactivate met
  last-admin-invariant.
- Fase C: cross-tenant onboarden (invite) direct in een doel-tenant
  — Zitadel user + activatie-mail + portal_user + personal-KB in
  tenant_scoped_session(target).
- Frontend: /admin/platform console (6 stat-cards, 5 tabs, zoek +
  refresh) + /admin/platform/orgs/{id} drill-down met invite-form +
  per-user rol-dropdown + suspend/reactivate.

Niet geverifieerd: de ingelogde UI-render (testbrowser uitgelogd).
Routing, gating, audit en alle SQL bevestigd tegen productie.

## 7. Niet in scope (v1)

- Echte MRR-berekening met prijstabel (stub €0 mag)
- Bewerken van users/orgs vanuit het platform-scherm (alleen lezen +
  doorlinken naar bestaande per-org admin); destructieve acties
  (deprovision) blijven via de bestaande endpoints
- Volledige chat-errors integratie (alleen recent-events stub)
