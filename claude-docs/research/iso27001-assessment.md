# ISO 27001:2022 Compliance Assessment — Klai AI Platform

> Code-gevalideerde analyse. Elke claim is geverifieerd in de broncode en configuratie.
> Datum: 2026-03-27 | 6 parallelle audit-agents (A.5, A.6+A.7, A.8.1–15, A.8.16–34, beleidskwaliteit, SOA-cross-validatie)
> Bijgewerkt: 2026-03-27 — SPEC-SEC-002 fixes verwerkt (A.6.5 GitHub offboarding, A.8.16 GlitchTip SMTP, A.8.13 restore test)

---

## Samenvatting

ISO 27001:2022 heeft 93 controls verdeeld over vier domeinen: A.5 (Org, 37 controls), A.6 (People, 8), A.7 (Physical, 14), A.8 (Technological, 34). De Klai SoA claimt bij SPEC-SEC-001 afsluiting: **59 COVERED (63,4%)**, **33 PARTIAL (35,5%)**, **1 GAP (A.5.6)**.

**Gecorrigeerd beeld na verdiepingsaudit (2026-03-27):**

| | Geclaimd | Werkelijk | Delta |
|---|---|---|---|
| COVERED | 59 | **≤ 49** | −10+ |
| PARTIAL | 33 | **≥ 42** | +9+ |
| GAP / FALSE | 1 | **≥ 5** | +4+ |

**Gesloten via SPEC-SEC-002 (2026-03-27):**
- **A.6.5** GitHub offboarding → ✅ COVERED: GitHub API service geïmplementeerd (`portal/backend/app/services/github.py`), GITHUB_ADMIN_PAT aanwezig
- **A.8.16** GlitchTip alerting → ✅ COVERED: `GLITCHTIP_EMAIL_URL` geconfigureerd via SOPS (SMTP → cloud86-host.io:587)
- **A.8.13** Restore testing → bewijs aanwezig: MongoDB-hersteltest uitgevoerd en gelogd (2026-03-27); overige gaps nog open

**Kritieke valse claims (COVERED maar NIET geïmplementeerd):**
- **A.7.10 / A.8.24** — "encrypted Hetzner block storage / at-rest encryption" → PostgreSQL, MongoDB, Redis hebben **geen** encryptie at-rest
- **A.5.34** — "GDPR by design" → Geen SAR-endpoint, geen gegevensportabiliteit, geen verwerkingsregister (AVG Art. 30)
- **A.8.7** — "malware protection" → Geen container image scanning (Trivy/Snyk/Grype)
- **A.8.13** — "backup" → Qdrant **niet** backed up, VictoriaLogs **niet** expliciet backed up
- **A.8.17** — "NTP on all Linux servers" → Geen NTP geïnstalleerd of gemonitord

**Tevens overstated (COVERED → PARTIAL):**
A.5.1, A.5.8, A.5.33, A.8.18, A.8.24, A.8.32
_(A.8.16 hersteld naar COVERED via SPEC-SEC-002)_

**Upgrade beschikbaar (PARTIAL → COVERED):**
A.7.7 (clear desk/screen — beleid bestaat al)

**Sterk punt:** Technische auth-, netwerk- en audit-fundering is solide (Zitadel OIDC, PKCE, Docker netwerksegregatie, PostgreSQL RLS, audit log). Het gat zit in at-rest encryptie, GDPR-rechten, operationele continuïteit, en beleidsnalatigheid.

---

## SOA-correctietabel — valse en overstated claims

| Control | ISO Vereiste | Geclaimd | Werkelijk | Bewijs |
|---------|-------------|---------|---------|--------|
| **A.5.1** | ISMS policies | COVERED | **PARTIAL** | CLAUDE.md mist formeel charter, management approval, scope, doelstellingen |
| **A.5.8** | InfoSec in projecten | COVERED | **PARTIAL** | OWASP niet formeel in SPEC-template, geen security review gate in CI |
| **A.5.33** | Gegevensbescherming | COVERED | **PARTIAL** | 30d retentie hardcoded (`-retentionPeriod=30d`), niet configureerbaar zoals geclaimd |
| **A.5.34** | Privacy & PII | **COVERED** | **FALSE** | Geen SAR-endpoint, geen portabiliteit, geen verwerkingsregister (AVG Art. 30) |
| **A.6.5** | Offboarding | **COVERED** | **COVERED ✅** | GitHub API service geïmplementeerd (SPEC-SEC-002); GITHUB_ADMIN_PAT aanwezig |
| **A.7.7** | Clean desk/screen | PARTIAL | **COVERED** ↑ | `endpoint-security.md` dekt dit volledig; upgrade gerechtvaardigd |
| **A.7.10** | Storage media | **COVERED** | **FALSE** | Docker volumes plaintext; geen LUKS/TDE/pgcrypto geconfigureerd |
| **A.8.7** | Anti-malware | **COVERED** | **FALSE** | Geen Trivy/Snyk in CI; Docker isolation ≠ malware protection |
| **A.8.13** | Backup | **COVERED** | **FALSE** | Qdrant en VictoriaLogs afwezig in `backup.sh`; backup-policy.md incorrect |
| **A.8.15** | Logging | COVERED | **PARTIAL** | 30d retentie hardcoded; ISO/NEN vereist langere retentie voor incidenten |
| **A.8.16** | Monitoring | COVERED | **COVERED ✅** | GlitchTip `GLITCHTIP_EMAIL_URL` geconfigureerd via SOPS (SMTP, SPEC-SEC-002) |
| **A.8.17** | Tijdsynchronisatie | **COVERED** | **FALSE** | `setup.sh` installeert geen NTP/chrony; `timedatectl` timezone ≠ NTP |
| **A.8.18** | Privileged utilities | COVERED | **PARTIAL** | `alloy` container draait als root (docker-compose.yml:509) |
| **A.8.24** | Cryptografie | **COVERED** | **PARTIAL** | TLS ✅; SOPS ✅; at-rest databases **niet** versleuteld |
| **A.8.32** | Change management | COVERED | **PARTIAL** | Geen GitHub branch protection rule; PR review niet afgedwongen |

---

## Bevindingen per ISO-domein

### A.5 — Organisatorische maatregelen

**Goed geïmplementeerd (✅):**
- A.5.2 Rollen en verantwoordelijkheden — goed gedocumenteerd in CLAUDE.md + SoA
- A.5.10 Aanvaardbaar gebruik — `acceptable-use.md` aanwezig
- A.5.15 Toegangsbeheersing — Zitadel OIDC + RBAC volledig uitgewerkt
- A.5.17 Authenticatie — sterk: TOTP, passkeys, email OTP, PKCE
- A.5.24–28 Incidentbeheer — IR-runbook aanwezig (`incident-response-runbook.md` v1.0.0, 293 regels), 6 fasen, P1/P2/P3-matrix, 72-uurs GDPR-meldtermijn

**Gaps (⚠️):**

**A.5.1 — ISMS-beleidsdocument:**
CLAUDE.md fungeert als informele ISMS-richtlijn maar mist verplichte ISO-elementen:
- Geen formele management approval
- Geen gedocumenteerde scope
- Geen documenthouder / versienummer
- Geen jaarlijks evaluatieschema

**A.5.8 — InfoSec in projectbeheer:**
MoAI SPEC-workflow bevat TRUST 5 met "Secured"-gate, maar:
- Geen formele security review stap in CI-pipeline
- Geen SAST (static application security testing) in `.github/workflows/`
- `moai-constitution.md` zegt OWASP; geen aantoonbare gate-enforcement per SPEC

**A.5.24–28 — Incidentbeheer:**
IR-runbook is uitgebreid maar:
- Nooit getest (geen tabletop-oefening, geen incidentlog)
- Geen actieve bewaking van incidentregistratie
- Status: PARTIAL (accurate; wordt COVERED na eerste test)

**A.5.33 — Gegevensbescherming:**
`docker-compose.yml:468`: `-retentionPeriod=30d` hardcoded. SOA claimt "configureerbaar" — dit is onjuist. Retentie is niet via portal of config aan te passen zonder compose-aanpassing + herstart.

**A.5.34 — Privacy & PII (KRITIEK):**
SOA claimt "GDPR by design" maar ontbreken volledig:
- **Geen SAR-endpoint** (AVG Art. 15 — recht op inzage)
- **Geen gegevensverwijdering API** (AVG Art. 17 — recht op vergetelheid, buiten offboarding)
- **Geen gegevensportabiliteit** (AVG Art. 20)
- **Geen verwerkingsregister** (AVG Art. 30 — verplicht, boetes tot €20M)
- **Geen DPIA** voor hoogrisicoverwerking (AVG Art. 35)

Dit is de meest materiële compliancegap in de volledige assessment.

---

### A.6 — Persoonsgebonden maatregelen

**Goed geïmplementeerd (✅):**
- A.6.1 Screening — `personnel-screening.md` aanwezig, 3-tier kader
- A.6.3 Bewustzijn en training — COVERED; MoAI workflow omvat security in elke SPEC-cyclus
- A.6.7 Remote working — COVERED; zero-trust (Zitadel OIDC), geen VPN nodig

**Gaps (⚠️):**

**A.6.5 — Offboarding (KRITIEK):**
`portal/backend/app/api/admin.py` → `offboard_user()`:
- ✅ Zitadel `deactivate_user()` aangeroepen
- ✅ Groepslidmaatschappen en producttoewijzingen verwijderd
- ✅ Audit event gelogd
- ❌ **GitHub org-verwijdering NIET geïmplementeerd**
- ❌ Geen revocatie van GitHub PATs of SSH keys
- ❌ Geen SOPS-sleutelrotatie trigger
- ❌ Geen verificatiestap na offboarding

Risico: voormalig medewerker behoudt GitHub-toegang en kan broncode, CI/CD-secrets, en infrastructuurconfiguraties blijven zien.

**A.6.2 — Arbeidsvoorwaarden:**
Geen expliciete informatiebeveiligingsclausule in arbeidscontract-template. NDA bestaat maar dekt alleen vertrouwelijkheid, niet actieve beveiligingsverantwoordelijkheden.

**A.6.8 — Meldingsproces security-events:**
Geen formeel proces voor teamleden om security-events te melden buiten GlitchTip. Geen `security@getklai.com`, geen SLA, geen escalatiepad.

---

### A.7 — Fysieke maatregelen

**Goed geïmplementeerd (✅):**
A.7.1–A.7.6, A.7.8, A.7.11–A.7.14 zijn volledig COVERED via Hetzner-datacenter verantwoordelijkheid (ISO 27001-gecertificeerd).

**Upgrade beschikbaar:**
A.7.7 (clear desk/screen) — `endpoint-security.md` dekt dit volledig; upgrade van PARTIAL naar COVERED gerechtvaardigd.

**Kritieke gap:**

**A.7.10 — Opslagmedia-encryptie (FALSE CLAIM):**
SOA claimt: "All data stored on encrypted Hetzner block storage; at-rest encryption enabled"

Verificatie in `deploy/docker-compose.yml`:
- PostgreSQL (lijnen 106–122): plain Docker volume, geen encryptie-opties
- MongoDB (lijnen 89–103): geen `--enableEncryption` flag
- Redis (lijnen 125–139): geen encryptie-opties
- Alle volumes: standaard Docker volumes zonder LUKS/TDE

**Wat WEL versleuteld is:**
- Transport: TLS via Caddy ✅
- Connector secrets: AES-256-GCM in `security.py` ✅
- Secrets in git: SOPS + age ✅
- Off-site backups: age-versleuteld voor upload naar Hetzner Storage Box ✅

**Wat NIET versleuteld is:**
- PostgreSQL database-bestanden at-rest
- MongoDB data at-rest (WiredTiger encryption niet geconfigureerd)
- Redis data at-rest
- Docker volumes op primaire opslag

De age-versleuteling in `backup.sh` geldt alleen voor off-site backup-kopieën, niet voor primaire data op schijf.

**A.7.9 — Security buiten kantoor:**
`endpoint-security.md` is uitgebreid (10 vereisten incl. FDE, screen lock, OS-updates). Maar:
- FDE niet geverifieerd op enig apparaat (self-attestation only)
- Geen MDM/EDR uitgerold
- Geen jaarlijkse endpoint-audit

---

### A.8 — Technologische maatregelen

#### A.8.1–15

**Goed geïmplementeerd (✅):**
- A.8.1 Endpoint devices — beleid aanwezig (`endpoint-security.md`)
- A.8.3 Informatiebeperkingen — RLS op 5 tabellen + audit log
- A.8.5 Authenticatie — volledig (PKCE, TOTP, passkeys, email OTP)
- A.8.9 Configuratiebeheer — docker-compose.yml als single source of truth
- A.8.10 Verwijdering informatie — offboarding endpoint wist data
- A.8.12 Data leakage prevention — interne Docker-netwerken, geen directe blootstelling
- A.8.14 Redundantie — Hetzner datacenter-redundantie

**Gaps (⚠️):**

**A.8.2 — Sleutelbeheer:**
SOPS + age werkt goed voor secrets-in-Git. Maar:
- Geen sleutelrevocatieprocedure gedocumenteerd (bij uittreden teamlid)
- Sleutelrotatie niet geautomatiseerd of gemonitord

**A.8.7 — Anti-malware (KRITIEK):**
SOA claimt "Linux; Docker isolation; Coolify." Container-isolatie is geen malware-bescherming.
- Geen Trivy, Snyk, Grype of vergelijkbare tool in `.github/workflows/`
- Alle Docker images draaien zonder vulnerability scanning
- MongoDB, Zitadel, LibreChat, vLLM — geen scan

Vereiste: Trivy of Snyk in CI-pipeline voor alle base images en productie-images.

**A.8.8 — Kwetsbaarheidsbeheer:**
`portal-api.yml:40-41`: `pip-audit --ignore-vuln CVE-2026-4539` — exception hardcoded in CI, niet gedocumenteerd in `vulnerability-management.md`. Policy definieert uitzonderingsproces; werkelijke uitzondering is niet gevolgd.

**A.8.13 — Backup (KRITIEK):**
`backup.sh` bevat:
- ✅ PostgreSQL (lijnen 66–70)
- ✅ Gitea tar.gz (lijnen 72–82)
- ✅ MongoDB mongodump (lijnen 85–93)
- ✅ Redis BGSAVE + dump.rdb (lijnen 95–101)
- ✅ Meilisearch snapshot (lijnen 103–112)
- ❌ **Qdrant: afwezig (grep: 0 resultaten)**
- ❌ **VictoriaLogs: afwezig (geen expliciete backup)**

`backup-policy.md` claimt Qdrant wekelijkse snapshots en VictoriaLogs wekelijkse backup. `asset-register.md` herhaalt beide claims. Alle drie zijn onjuist.

Qdrant is bewust uitgesloten als "derived index" (herbouwbaar via ingest-pipeline), maar dit is nergens in de policy gedocumenteerd.

Aanvullend: restoration testing (A.8.13 vereiste) heeft nooit plaatsgevonden. Geen RTO/RPO-documentatie. Kwartaalse MongoDB-hersteltest, halfjaarlijkse Qdrant-test, jaarlijkse volledige servertest zoals beschreven in policy zijn nooit uitgevoerd.

**A.8.15 — Logging:**
30-daagse retentie hardcoded in `docker-compose.yml:468`. PARTIAL (accurate): bestaat maar is niet configureerbaar zoals geclaimd. Voor NEN 7510-compliance (zorgsector) vereist 6–12 maanden.

---

#### A.8.16–34

**Goed geïmplementeerd (✅):**
- A.8.19 Software installatie — alles via Docker/Coolify, geen ad-hoc
- A.8.20 Netwerkbeveiliging — Caddy TLS, UFW, interne Docker-netwerken
- A.8.21 Netwerkdiensten — alles achter reverse proxy of intern
- A.8.22 Netwerksegregatie — 8 Docker-netwerken, allemaal `internal: true`
- A.8.23 Web filtering — Caddy URL-routing + blokkeert ongeautoriseerde toegang
- A.8.25 Secure development lifecycle — MoAI SPEC-workflow + CLAUDE.md
- A.8.26 Applicatiebeveiligingsvereisten — OWASP in SPEC, Pydantic-validatie
- A.8.27 Veilige systeemarchitectuur — zero-trust + per-tenant isolatie + EU-only
- A.8.28 Secure coding — SQLAlchemy ORM (geen raw queries), geen XSS, PKCE
- A.8.30 Uitbesteding ontwikkeling — volledig intern
- A.8.33 Testinformatie — synthetische data, geen productiedata in tests
- A.8.34 Auditbescherming — read-only VictoriaLogs, rol-gated Grafana, append-only audit log

**Gaps (⚠️):**

**A.8.16 — Monitoringactiviteiten:**
VictoriaMetrics + Grafana + cAdvisor ✅. Maar GlitchTip `EMAIL_URL=consolemail://` (docker-compose.yml:385, 416): alerts gaan naar console-output, niet naar email of webhook. SOA-claim "GlitchTip alerting" is onjuist.

**A.8.17 — Tijdsynchronisatie (FALSE CLAIM):**
`deploy/setup.sh` installeert: curl, git, htop, unzip, ufw, fail2ban — **geen** ntp, chrony, of systemd-timesyncd verificatie. `timedatectl set-timezone Europe/Helsinki` op lijn 37 stelt timezone in, maar dit is niet hetzelfde als NTP.

Hetzner-VMs hebben mogelijk systemd-timesyncd standaard actief, maar dit is nooit geverifieerd, gemonitord, of getest. Geen health check, geen Uptime Kuma monitor.

**A.8.18 — Geprivilegieerde hulpprogramma's:**
`docker-compose.yml:509`: `user: root` voor Alloy (metrics collector) — vereist Docker socket. Alloy is geen `privileged: true` container maar draait wel als root. SOA-claim "no privileged containers" is technisch correct maar semantisch misleidend.

**A.8.24 — Cryptografie:**
Zie A.7.10. TLS ✅, SOPS ✅, maar PostgreSQL/MongoDB/Redis geen at-rest encryptie. SOA-claim "Hetzner encrypted block storage" is niet geverifieerd en tegenstrijdig met NEN 7510-audit.

**A.8.29 — Beveiligingstesten:**
`pip-audit` in CI ✅. Maar geen SAST (Semgrep/CodeQL), geen DAST (OWASP ZAP), geen formeel penetratietestprogramma. Status PARTIAL is accurate.

**A.8.31 — Scheiding omgevingen:**
Productie op core-01 ✅. Maar geen formele staging-omgeving. CI-pipeline deployt direct naar productie na groen lint. PARTIAL is accurate.

**A.8.32 — Wijzigingsbeheer:**
Git-tracking ✅, GitHub Actions CI ✅, SPEC-workflow ✅, audit logging ✅. Maar geen GitHub branch protection rule gevonden. CI passeert, maar PR-review is niet verplicht gesteld. SOA-claim "PR review required" is niet aantoonbaar afgedwongen.

---

## Beleidsdocument-kwaliteit

Vijf beleidsdocumenten zijn geaudit tegen werkelijke implementatie:

### backup-policy.md — Kritieke onnauwkeurigheden

| Claim in policy | Werkelijkheid (backup.sh) | Ernst |
|---|---|---|
| "Hetzner volume encryption" | `age` (ChaCha20-Poly1305) voor off-site transfer | HOOG |
| "Qdrant: Weekly snapshot API" | Qdrant afwezig in backup.sh | HOOG |
| "VictoriaLogs: Weekly volume backup" | Ingebouwde 30d retentie, geen expliciete backup | MEDIUM |
| "MongoDB: 30 days retention" | Correct (code: `head -n -30`; commentaar "7 days" is bug) | LAAG |
| Kwartaalse restoration testing | Nooit uitgevoerd; geen RTO/RPO-documentatie | HOOG |

### vulnerability-management.md — Ontbrekende uitzonderingsdocumentatie

| Gap | Ernst |
|---|---|
| CVE-2026-4539 genegeerd in CI zonder gedocumenteerde uitzondering | KRITIEK |
| Container image scanning gepland maar niet geïmplementeerd | KRITIEK |
| Geen compenserende-controlelog bijgehouden | MEDIUM |
| Geen maandelijkse/kwartaalse vulnerability review geregistreerd | MEDIUM |

### acceptable-use.md — Handhaving ontbreekt

| Gap | Ernst |
|---|---|
| FDE-vereiste zonder verificatiemechanisme | MEDIUM |
| BYOD-nalevingscheck niet operationeel | MEDIUM |
| MFA-beleid niet beschreven (bestaat elders als veld in DB) | MEDIUM |

### personnel-screening.md — Onvolledige eerste-aanstelling-transitie

| Gap | Ernst |
|---|---|
| Geen stap-voor-stap procedure voor eerste externe aanstelling | MEDIUM |
| VOG vereist voor Tier 3, geen equivalent voor niet-Nederlandse kandidaten | LAAG |

---

## Verdiepingsaudit: Kloppende vs onjuiste SoA-claims

### Volledig kloppen (geselecteerde steekproef)
A.7.1–7.6, A.7.8, A.7.11–7.14 (Hetzner datacenter) ✅
A.8.19–23, A.8.25–28, A.8.30, A.8.33–34 (technische controls) ✅
A.6.7 (remote working) ✅
A.5.15, A.5.17 (toegang en authenticatie) ✅

### Upgrades beschikbaar
A.7.7 (clear desk/screen) → COVERED

### Downgrade vereist
Zie SOA-correctietabel bovenaan.

---

## Prioritaire acties

### Kritiek — vóór externe audit of zorghealthcare-klant

| # | Actie | Control(s) | Bewijs |
|---|---|---|---|
| 1 | Container image scanning implementeren (Trivy/Snyk in CI) | A.8.7, A.8.8 | Geen scanner in `.github/workflows/` |
| 2 | A.7.10/A.8.24 at-rest encryptie: Hetzner bevestigen OF databases versleutelen | A.7.10, A.8.24 | `docker-compose.yml` volumedefinities; NEN-audit |
| 3 | SOA A.5.34 downgraden naar PARTIAL; verwerkingsregister aanleggen | A.5.34 | AVG Art. 30 verplicht; geen register aangetroffen |
| 4 | Backup-policy.md corrigeren (age vs Hetzner; Qdrant scope) | A.8.13 | `backup.sh` vs policy mismatch |
| 5 | A.8.17 NTP: explicit installatie + health check in setup.sh | A.8.17 | `setup.sh` installeert geen NTP |
| 6 | CVE-2026-4539 uitzondering documenteren (GitHub Issue + policy-update) | A.8.8 | `portal-api.yml:41` undocumented ignore |
| 7 | SAR-endpoint implementeren (AVG Art. 15) | A.5.34 | Geen endpoint in codebase |

### Hoog — binnen 1 maand

| # | Actie | Control(s) | Bewijs |
|---|---|---|---|
| 8 | GitHub-verwijdering toevoegen aan `offboard_user()` | A.6.5 | `admin.py` offboarding ontbreekt GitHub API call |
| 9 | GitHub branch protection op `main` inschakelen (1 approval vereist) | A.8.32 | Branch protection niet aangetroffen in repo |
| 10 | GlitchTip SMTP of webhook configureren | A.8.16 | `EMAIL_URL=consolemail://` in docker-compose.yml |
| 11 | Restoration testing schema opstellen + eerste test uitvoeren | A.8.13 | Nooit getest; policy beschrijft kwartaalschema |
| 12 | VictoriaLogs retentie ophogen voor NEN-klanten (6–12 maanden) | A.8.15, A.5.33 | `-retentionPeriod=30d` hardcoded |

### Gemiddeld — binnen 3 maanden

| # | Actie | Control(s) | Bewijs |
|---|---|---|---|
| 13 | SOPS sleutelrevocatieprocedure documenteren | A.8.2 | Geen procedure bij uittreden teamlid |
| 14 | Formeel ISMS-charter opstellen (A.5.1) | A.5.1 | CLAUDE.md mist management-charter-elementen |
| 15 | Verwerkingsregister opstellen (AVG Art. 30) | A.5.34 | Verplicht; niet aangetroffen |
| 16 | DPIA uitvoeren voor hoogrisicoverwerkingen | A.5.34 | AVG Art. 35; niet aangetroffen |
| 17 | Endpoint security FDE-verificatieprocedure implementeren | A.7.9, A.6 | `acceptable-use.md` mist handhaving |
| 18 | Eerste-aanstellingsroute in `personnel-screening.md` | A.6.1 | Overgang founders → eerste externe hire onbeschreven |
| 19 | SAST toevoegen aan CI (Semgrep of CodeQL) | A.8.29 | Geen SAST gevonden in workflows |
| 20 | `acceptable-use.md` MFA-sectie toevoegen | A.6, A.8.5 | MFA-beleid beschreven in DB maar niet in AUP |

### Laag — nice-to-have

| # | Actie | Control(s) |
|---|---|---|
| 21 | A.7.7 upgraden naar COVERED in SoA | A.7.7 |
| 22 | A.5.6 (contacten externe partijen) beleid opstellen | A.5.6 |
| 23 | Staging-omgeving opzetten | A.8.31 |
| 24 | backup.sh code-commentaar corrigeren ("7 dagen" → "30 dagen") | A.8.13 |
| 25 | NDA-template formaliseren en in repo opslaan | A.6.6 |
| 26 | Security-meldproces definiëren (`security@getklai.com` + SLA) | A.6.8 |

---

## Gecorrigeerde compliance-schatting

Na adressering van alle kritieke en hoge prioriteitsitems:

| Domein | Huidig | Na fixes |
|---|---|---|
| A.5 Organisatorisch | ~55% | ~75% |
| A.6 Mensen | ~65% | ~85% |
| A.7 Fysiek | ~85% | ~90% (A.7.10 fix vereist) |
| A.8 Technologisch | ~65% | ~80% |
| **Totaal** | **~63%** | **~80%** |

De 20% gap na hoge-prioriteitsfixes betreft structurele gaten: verwerkingsregister (AVG Art. 30), DPIA, SAR-endpoint, en staging-omgeving — die 1–3 maanden implementatiewerk vereisen.

---

## Relatie tot NEN 7510-assessment

NEN 7510 is ISO 27001 + zorgsectoraanvullingen. De overlappende bevindingen zijn consistent:

| Bevinding | NEN 7510 | ISO 27001 | Status |
|---|---|---|---|
| Container image scanning | Kritiek (item 13) | A.8.7, A.8.8 | Beide open |
| At-rest encryptie databases | Open | A.7.10, A.8.24 | Beide open |
| Log retentie < 6 maanden | Open (item 15) | A.8.15, A.5.33 | Beide open |
| SAR / verwerkingsregister | Open (items 18–19) | A.5.34 | Beide open |
| Backup-scope (Qdrant) | Open | A.8.13 | Beide open |
| Restoration testing | Open (item 24) | A.8.13 | Beide open |
| NTP synchronisatie | — | A.8.17 | ISO-specifiek |
| GitHub offboarding | — | A.6.5 | ISO-specifiek |
| Branch protection | — | A.8.32 | ISO-specifiek |
| GlitchTip alerting | — | A.8.16 | ISO-specifiek |

De ISO 27001 assessment voegt **4 nieuwe kritieke bevindingen** toe die de NEN 7510 audit niet adresseerde: NTP (A.8.17), GitHub offboarding (A.6.5), branch protection (A.8.32), en GlitchTip alerting (A.8.16).
