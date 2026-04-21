# Vexa Stack Security Audit — SEC-013 input

**Datum:** 2026-04-19
**Scope:** `deploy/vexa/`, vexa services in `deploy/docker-compose.yml`, `klai-portal/backend/app/api/meetings.py`, Caddy `/bots/*` route
**Status Vexa stack:** in migratie naar `upstream/main` v0.10 track (SPEC-VEXA-003). Momenteel draait alleen `klai-core-vexa-redis-1` in prod; rest van stack in rollout.
**Relevantie:** Vexa is een **extern AI-project in actieve ontwikkeling** — supply-chain risico is standing concern.

## TL;DR

Vexa-migratie lost een aantal v1 issues op (`:latest` tag-antipattern, webhook dedup, tier-based admission), maar introduceert nieuwe risk surface:

- **V-001** [HIGH]: `/bots/*` Caddy route wijst naar **non-existent service** `vexa-bot-manager:8000` — dode route in productie
- **V-002** [HIGH]: `runtime-api` mount `/var/run/docker.sock` met `docker:988` groep — **Docker-socket escape-risk**
- **V-003** [MEDIUM]: `CORS_ORIGINS: "*"` op `api-gateway` — open CORS voor publieke Vexa API
- **V-004** [MEDIUM]: Portal webhook trust van `172.x/10.x/192.168.x` IP-ranges — container-escape = bypass webhook-auth
- **V-005** [MEDIUM]: `ALLOW_PRIVATE_CALLBACKS: "1"` op runtime-api — mogelijk SSRF-surface
- **V-006** [LOW]: `TRANSCRIPTION_SERVICE_TOKEN: internal` hardcoded placeholder — geen echte auth gpu-01 ↔ meeting-api
- **V-007** [INFO]: Supply chain — 5 externe `vexaai/*` images per build, upstream in active development
- **V-008** [INFO]: vexa-bots containers spawned dynamically via Docker socket → geen traditional image-scan coverage

Geen CRITICAL findings ontdekt op design-niveau. De HIGH findings zijn operationele exposure-issues die beheersbaar zijn.

---

## Architectuur overzicht (SPEC-VEXA-003 v0.10)

```
Caddy /bots/*
  └─→ vexa-bot-manager:8000  [DOES NOT EXIST — dead route V-001]

Vexa v0.10 stack (new):
  ┌─ klai-net ──────────────────────────────────────────────┐
  │  api-gateway   ── public entry (RATE_LIMIT_RPM=120)     │
  │     ├── admin-api          (v1 admin ops)               │
  │     └── meeting-api        (bot lifecycle + webhooks)   │
  │            └── runtime-api (spawns bot containers)      │
  │                    ├── docker.sock (V-002)              │
  │                    └── vexa-bots network                │
  └─────────────────────────────────────────────────────────┘

  ┌─ vexa-bots (isolated) ──────────────────────────────────┐
  │  vexa-redis (pub/sub + streams)                         │
  │  vexaai/vexa-bot:0.10.0-*  (ephemeral, spawned)         │
  └─────────────────────────────────────────────────────────┘

  portal-api ─(meeting.completed webhook)─> api-gateway
                                              └── meeting-api internal HTTP
                                                    └── portal-api /api/bots/internal/webhook
```

## Findings

### V-001 — `/bots/*` Caddy route wijst naar non-existent service [HIGH]

**Locatie:** `deploy/caddy/Caddyfile`:

```caddy
handle /bots/* {
    rate_limit { zone bots_per_ip { events 10 ; window 1m } }
    uri strip_prefix /bots
    reverse_proxy vexa-bot-manager:8000
}
```

**Probleem:**
- `vexa-bot-manager:8000` bestaat niet (meer) in `deploy/docker-compose.yml`. Service die Caddy adresseert heet nu **`api-gateway`**.
- Gevolg: elke call naar `*.getklai.com/bots/*` → Caddy proberen te reverse-proxyen → DNS-resolve fail of connection refused → 502/504 naar client.
- Extern toegankelijk met rate-limit 10/min/IP, dus niet bruikbaar als DoS-reflector.

**Impact:**
- **Functional**: elke feature die `/bots/*` gebruikt is kapot (mogelijk al — huidige prod-run heeft geen Vexa gateway running)
- **Security**: dode route in publieke surface = extra attack vector (als iemand later `vexa-bot-manager:8000` per ongeluk exposed, publiek routed zonder review)

**Aanbeveling:**
1. Migreer Caddy route naar nieuwe service-naam: `reverse_proxy api-gateway:8000`
2. OF (als `/bots/*` niet meer nodig is — portal-api gebruikt meeting-api direct via container-network): **verwijder de hele handle-block** uit Caddyfile
3. Verifieer welke (externe) clients op `/bots/*` vertrouwen — likely niemand, want vexa-bot-manager image werd vervangen

### V-002 — runtime-api mount `docker.sock` + docker group [HIGH — acceptable with mitigations]

**Locatie:** `deploy/docker-compose.yml` runtime-api block:

```yaml
runtime-api:
  group_add:
    - "988"   # docker GID on core-01 — required for socket access
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
```

**Probleem:**
- runtime-api heeft **root-equivalent access** op de Docker daemon via socket. Compromise van runtime-api → eigenaar van host's Docker.
- Docker socket access = create containers met `--privileged`, mount host filesystem, escape naar host.

**Mitigerend:**
- runtime-api draait op `vexa-bots` + `net-postgres` netwerken — **niet direct publiek bereikbaar** via Caddy
- Aangeroepen alleen door meeting-api via `http://runtime-api:8090` (intern network)
- `BOT_API_TOKEN` als auth gate

**Waarom niet een docker-socket-proxy?**
- portal-api gebruikt `tcp://docker-socket-proxy:2375` met filter (CONTAINERS/NETWORKS/POST/DELETE only — zie `.claude/rules/klai/platform/caddy.md`)
- runtime-api kan hetzelfde pattern volgen, maar momenteel doet het dat niet

**Aanbeveling:**
1. **Kort-termijn acceptabel**: mitigaties via network-isolatie + internal token
2. **Mid-termijn**: migreer runtime-api achter `docker-socket-proxy` met whitelist voor alleen container-spawn (CONTAINERS + POST + DELETE + nothing else)
3. **Lang-termijn**: overweeg rootless docker of sysbox-runc voor bot-containers (upstream Vexa roadmap?)

### V-003 — api-gateway CORS `*` [MEDIUM]

**Locatie:** `deploy/docker-compose.yml` api-gateway block:

```yaml
environment:
  CORS_ORIGINS: "*"
```

**Probleem:**
- Caddy exposed api-gateway (via `/bots/*` route when that gets fixed, or directly if CADDY routes toevoegen).
- Wildcard CORS betekent dat elke browser-origin authenticated calls kan maken als ze een geldige Bearer token hebben.
- Voor publieke API bedoeld voor browser-embeds (widget-achtig) kan dit legitiem zijn, maar moet expliciet zijn.

**Aanbeveling:**
1. Bepaal expliciete lijst: alleen `*.getklai.com` + known widget-embedding origins
2. Update naar `CORS_ORIGINS: "https://my.getklai.com,https://*.getklai.com"` (formaat hangt af van wat api-gateway ondersteunt)
3. Vraag Vexa-upstream hoe ze configureren

### V-004 — Portal webhook trust via IP-range [MEDIUM]

**Locatie:** `klai-portal/backend/app/api/meetings.py:48-55`:

```python
def _require_webhook_secret(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    if client_host.startswith(("172.", "10.", "192.168.")):
        return   # trust Docker network callers
    if not settings.vexa_webhook_secret:
        return  # No secret configured — fail-open fallback!
    ...
```

**Problemen:**
1. **Docker network trust**: meeting-api roept portal-api aan via Docker bridge network. `request.client.host` komt daar inderdaad uit 172.x, maar als iemand via Caddy-proxy komt met `X-Forwarded-For` gespoofed, dan **ziet portal-api nog steeds de Docker-IP van Caddy** — dus acceptabel. Maar: een compromised Vexa-container binnen klai-net heeft dan ook unrestricted webhook-access.
2. **Fail-open op `vexa_webhook_secret` empty** — zelfde anti-pattern als F-003 / F-006 / F-007. Als env var niet gezet is → geen auth voor externe calls.

**Aanbeveling:**
1. Fail-closed: startup-validator dat `vexa_webhook_secret` niet leeg mag zijn (zelfde patroon als SEC-011)
2. Overweeg IP-range-trust helemaal te verwijderen: als meeting-api naast portal-api staat, eis een secret voor **alle** callers
3. Alternatief: cryptographic webhook signatures (HMAC-SHA256 over body) — meer robuust dan shared secret

**Mark as:** `F-030` in unified findings (aansluiten bij F-series)

### V-005 — ALLOW_PRIVATE_CALLBACKS=1 op runtime-api [MEDIUM]

**Locatie:** `deploy/docker-compose.yml` runtime-api block:

```yaml
environment:
  ALLOW_PRIVATE_CALLBACKS: "1"
```

**Probleem:** Betekent dat runtime-api HTTP-callbacks naar **private IP ranges** kan maken (172.x, 10.x, 192.168.x, 127.x). Typisch SSRF-protection is dat je private IPs blokkeert. Hier expliciet uit.

**Impact:**
- Als een user een meeting-URL kan instellen die eventueel in een callback komt, kunnen ze potentieel interne services bereiken
- Ons gebruik: POST_MEETING_HOOKS gaat naar `http://portal-api:8010/api/bots/internal/webhook` (Docker-intern). Dit is **waarom** `ALLOW_PRIVATE_CALLBACKS=1` aanstaat.

**Mitigerend:**
- POST_MEETING_HOOKS is door ops ingesteld, niet door user
- Interne netwerk isolation

**Aanbeveling:**
1. Verifieer bij Vexa upstream of `ALLOW_PRIVATE_CALLBACKS` een expliciete allowlist per URL ondersteunt (ipv. alles-aan/alles-uit)
2. Documenteer waarom dit aanstaat in `deploy/docker-compose.yml` comment
3. Monitor: welke callbacks maakt runtime-api? Log alle uitgaande HTTP-calls via Alloy

### V-006 — `TRANSCRIPTION_SERVICE_TOKEN: internal` hardcoded [LOW]

**Locatie:** `deploy/docker-compose.yml` meeting-api:

```yaml
TRANSCRIPTION_SERVICE_URL: http://172.18.0.1:8000/v1/audio/transcriptions
TRANSCRIPTION_SERVICE_TOKEN: internal
```

**Probleem:** De value is de letterlijke string `internal`, niet een env-var. Dus token = `internal`. Transcription service (whisper-server op gpu-01 via ssh tunnel 172.18.0.1:8000) accepteert elke client met dat shared-secret.

**Impact:**
- GPU-01 whisper service staat achter autossh tunnel, alleen bereikbaar van core-01's Docker bridge
- Dit is dev-placeholder, niet een echte secret

**Aanbeveling:**
1. Vervang door env-var sourced from SOPS: `TRANSCRIPTION_SERVICE_TOKEN: ${WHISPER_SERVICE_TOKEN}`
2. Genereer een random 32-byte token in SOPS
3. Update whisper-server om dezelfde token te verifiëren

### V-007 — Supply chain: 5 externe vexaai/* images per build [INFO — monitor]

Vexa stack gebruikt:
- `vexaai/admin-api:0.10.0-260419-1129`
- `vexaai/api-gateway:0.10.0-260419-1129`
- `vexaai/meeting-api:0.10.0-260419-1129`
- `vexaai/runtime-api:0.10.0-260419-1129`
- `vexaai/vexa-bot:0.10.0-260419-1129`

**Risico's:**
1. Vexa is actief in ontwikkeling. Upstream security-patches landen mogelijk zonder CVE-ID
2. Image bevat browser-automation (Chromium) — grote attack surface (CVE-stroom)
3. Klai ontvangt images via Docker Hub (geen signed manifests, geen SBOM tenzij expliciet)

**Mitigerend:**
- Pin op exacte versie `0.10.0-260419-1129` — geen `:latest` drift (SPEC-VEXA-003 REQ-I-xxx)
- Trivy weekly scan loopt (parallel session CVE infra) — **maar** Trivy kan onbekende CVE's niet detecteren
- Isolated `vexa-bots` network

**Aanbeveling:**
1. **Monitor upstream**: subscribe op `github.com/Vexa-ai/vexa` releases
2. **Image-rebuild cadence**: bij elke upstream release → Trivy scan → als groen, pin nieuwe tag
3. Overweeg eigen fork met cherry-picked security-patches als upstream traag is

### V-008 — vexa-bot containers spawned via Docker socket [INFO — by design]

`runtime-api` spawnt op-demand `vexaai/vexa-bot:0.10.0-*` containers. Deze containers:
- Bevatten Chromium (browser attack surface)
- Mounten audio-recording volumes
- Joinen `vexa-bots` netwerk

**Risico's:**
- Elk bot-container is ephemeral maar kan > 1 uur leven bij lange meetings
- Als Chromium RCE via de meeting-pagina → bot-container compromised
- Bot-container kan pingen naar vexa-redis (dezelfde network) en mogelijk data exfiltreren

**Mitigerend:**
- `vexa-bots` network is isolated (geen uitgaand internet volgens SPEC-VEXA-003, maar het **heeft** `bridge` mode voor het joinen van meetings — nodig om Google Meet/Zoom te bereiken)
- Geen secrets gemount in bot-containers

**Aanbeveling:**
1. Verify: heeft `vexa-bots` network `internal: true` of heeft het externe internet?
2. Als internet nodig (voor meeting-join): overweeg egress-firewall (bv. alleen poorten 443/80 naar externe, geen interne resources)
3. Log bot-container dns/network activity via Alloy

---

## Findings unified met audit-series

Voor consistency met de bestaande F-xxx naming, voeg ik ze toe aan de roadmap:

| Vexa ID | Unified ID | SEC-fix group |
|---|---|---|
| V-001 | F-030 | SEC-013 |
| V-002 | F-031 | SEC-013 |
| V-003 | F-032 | SEC-013 |
| V-004 | F-033 | SEC-013 (existing webhook auth — fix nu kan, los van Vexa rebuild) |
| V-005 | F-034 | SEC-013 |
| V-006 | F-035 | SEC-013 (trivial) |
| V-007 | F-036 | ongoing supply-chain monitoring |
| V-008 | F-037 | SEC-013 |

## SEC-013 expanded scope

SEC-013 wordt nu:
1. **Original (deferred from SEC-008):** docs-app audit (F-022)
2. **Vexa audit (NEW):** F-030..F-037 uit dit doc

Voorstel SEC-013 wordt SEC-013a (Vexa) + SEC-013b (docs-app) als aparte tickets.

## Immediate actionable (kan vóór Vexa-rollout doorgevoerd)

**Binnen klai-portal (onafhankelijk van Vexa-migratie):**
1. **F-033 fix**: `_require_webhook_secret` fail-closed + optionele HMAC-signing. Klein patch.

**Binnen klai-infra (na Vexa rollout):**
2. **F-030 fix**: Caddyfile `/bots/*` route — migreer naar `api-gateway:8000` OF verwijder

**Bij upstream Vexa config (pre-rollout):**
3. **F-032**: CORS_ORIGINS explicit lijst
4. **F-035**: Vervang `TRANSCRIPTION_SERVICE_TOKEN: internal` door SOPS-var

## Parking lot (nog uit te zoeken)

- [ ] **V-008 deep-dive**: network-mode van `vexa-bots` — kan bot-container echt naar internet?
- [ ] **V-002 alternatief**: werkt Vexa's runtime-api met docker-socket-proxy? Vraag upstream.
- [ ] **V-005 scope**: wat zijn alle callback URLs die runtime-api ooit maakt? Runtime audit tijdens eerste bot-run.
- [ ] **V-007 action**: wie heeft ops-ownership voor Vexa-image-updates? Kalender-cadans?

## Changelog

| Datum | Wijziging |
|---|---|
| 2026-04-19 | Initiële Vexa audit. 8 findings (V-001..V-008 / F-030..F-037). Geen CRITICAL, 2 HIGH (dode Caddy route, docker.sock mount), 4 MEDIUM, 2 INFO. |
