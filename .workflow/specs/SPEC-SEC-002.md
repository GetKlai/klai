# SPEC-SEC-002: ISO 27001 Compliance — Kleine en middelgrote items

## Metadata

- **SPEC ID:** SPEC-SEC-002
- **Titel:** ISO 27001:2022 Compliance — Kleine en middelgrote gaps
- **Status:** DRAFT
- **Prioriteit:** High
- **Aangemaakt:** 2026-03-27
- **Voorganger:** SPEC-SEC-001 (initieel ISO 27001 beleidskader)
- **SOA-referenties:** A.8.7, A.8.29, A.8.8, A.8.32, A.8.17, A.8.16, A.6.5, A.8.13, A.5.34, A.6.1, A.5.10/A.8.5, A.8.2, A.7.7

---

## Scope

Deze SPEC dekt **14 kleine en middelgrote compliance-items** geidentificeerd tijdens een ISO 27001:2022 verdiepingsaudit. De items zijn gegroepeerd in vier categorieen:

1. **CI/CD security** (R1-R4): Container scanning, SAST, CVE-documentatie, branch protection
2. **Infrastructuur** (R5-R6): NTP-synchronisatie, GlitchTip alerting
3. **Applicatie** (R7): GitHub-verwijdering in offboarding
4. **Beleidsdocumenten** (R8-R14): Correcties backup-policy, verwerkingsregister, procedures, kleine fixes

### Out of scope

De volgende **grote items** zijn expliciet buiten scope:

- At-rest encryptie voor databases (PostgreSQL TDE / MongoDB encryption at rest)
- Subject Access Request (SAR) endpoint in portal-api
- Data Protection Impact Assessment (DPIA) document
- A.5.6 (Contact met speciale belangengroepen) — apart gedeferred

---

## Achtergrond en marktstandaard per item

### R1 — Container image scanning (A.8.7)

**Huidige staat:** Geen vulnerability scanning op Docker images in CI. De `portal-api.yml` workflow bouwt en pusht images naar GHCR zonder scan.

**Marktstandaard:** Trivy (Aqua Security) is de de-facto standaard voor open-source container scanning. Het wordt door >80% van de CNCF-community gebruikt. Trivy scant OS-packages, language-dependencies, en IaC-configuraties in een enkele tool. De officiele `aquasecurity/trivy-action` integreert direct met GitHub Actions en ondersteunt SARIF-output voor GitHub Code Scanning.

**Aanbeveling:** Trivy via `aquasecurity/trivy-action@0.33.1`:
- Gratis, geen account nodig, geen API-key
- Severity-filter op `CRITICAL,HIGH` voorkomt ruis van low-severity findings
- `ignore-unfixed: true` onderdrukt CVEs zonder beschikbare patch
- SARIF-output naar GitHub Security tab voor inzichtelijkheid
- Alternatieven (Snyk, Grype, Docker Scout) vereisen accounts of zijn minder geintegreerd met GitHub Actions

### R2 — SAST toevoegen (A.8.29)

**Huidige staat:** Geen static application security testing. De CI-pipeline heeft ruff (linting), pyright (types), en pip-audit (dependencies), maar geen security-specifieke code-analyse.

**Marktstandaard:** Semgrep is de lichtste SAST-tool voor Python/TypeScript-codebases:
- Open-source core met 2500+ community-regels
- Geen account nodig voor de CLI (`semgrep scan --config auto`)
- Python en TypeScript worden both first-class ondersteund
- GitHub Actions integratie via `semgrep/semgrep-action`
- Veel lagere false-positive rate dan CodeQL (dat zwaar is en 15+ minuten draait)
- SonarCloud vereist een account, dashboard, en is overkill voor een 4-persoonsteam

**Aanbeveling:** Semgrep met `--config auto` (best-practice regels) + `--config p/owasp-top-ten` voor OWASP-dekking. Draaitijd ~2 minuten voor de portal codebase.

### R3 — CVE-uitzondering documenteren (A.8.8)

**Huidige staat:** Regel 41 in `portal-api.yml` bevat `pip-audit --ignore-vuln CVE-2026-4539` zonder enige documentatie waarom deze CVE wordt geignoreerd.

**Marktstandaard:** Elke CVE-uitzondering hoort gedocumenteerd te zijn met:
- Reden voor uitzondering (niet exploiteerbaar, compenserende controle, wachtend op patch)
- Einddatum (herbeoordelingsdatum)
- Verantwoordelijke persoon
- Referentie naar een GitHub Issue voor traceerbaarheid

**Aanpak:** GitHub Issue aanmaken, referentie als commentaar in workflow, registratie in `vulnerability-management.md`.

### R4 — GitHub branch protection (A.8.32)

**Huidige staat:** De SoA claimt A.8.32 als COVERED met "PR review required", maar er is geen branch protection rule op `main`. Iedereen kan direct pushen.

**Marktstandaard:** Standaard voor ISO 27001: minimaal require PR review (1 reviewer), status checks moeten passen, force-push geblokkeerd. Via `gh api` of GitHub UI configureerbaar.

**Aanpak:** `gh api` call vanuit een admin-account om branch protection in te stellen. Geen code nodig.

### R5 — NTP/tijdsynchronisatie (A.8.17)

**Huidige staat:** `deploy/setup.sh` zet alleen de timezone (`timedatectl set-timezone Europe/Helsinki`). Dit synchroniseert **niet** de klok met een NTP-bron. De SoA claimt A.8.17 als COVERED met "standard Linux NTP", maar `setup.sh` installeert geen NTP-daemon.

**Marktstandaard:** Moderne Ubuntu/Debian-servers (22.04+) hebben `systemd-timesyncd` standaard geinstalleerd en actief. Dit is een lichtgewicht NTP-client die voldoende is voor >99% van de gevallen. `chrony` is het alternatief voor servers die nauwkeurigere synchronisatie nodig hebben (sub-milliseconde), maar dat is hier niet relevant. `ntpd` (het klassieke NTP-pakket) wordt niet meer aanbevolen op moderne systemen.

**Verificatie nodig:** Controleer of `systemd-timesyncd` daadwerkelijk actief is op core-01. Zo ja: documenteer dit als bewijs in de SoA. Zo nee: activeer `timedatectl set-ntp true` in `setup.sh`.

**Aanpak:** Voeg `timedatectl set-ntp true` toe aan `setup.sh` als expliciete stap, en verifieer met `timedatectl timesync-status`. Dit maakt de claim aantoonbaar.

### R6 — GlitchTip external alerting (A.8.16)

**Huidige staat:** `EMAIL_URL: "consolemail://"` in `docker-compose.yml` (regels 385 en 416). Dit betekent dat alle alerts naar de container stdout gaan — niemand wordt gewaarschuwd tenzij iemand actief logs leest.

**Marktstandaard:** GlitchTip ondersteunt SMTP via `EMAIL_URL` in django-environ formaat: `smtp://user:password@host:port`. Daarnaast zijn Mailgun, SendGrid, en andere Anymail-backends beschikbaar. Webhook-notificaties worden **niet** native ondersteund door GlitchTip.

**Aanpak voor Klai:** Klai gebruikt Cloud86 als SMTP-relay. De `EMAIL_URL` moet worden ingesteld op de Cloud86 SMTP-credentials. Dit vereist:
1. SMTP-credentials aanmaken of ophalen bij Cloud86
2. `EMAIL_URL` bijwerken in `.env.sops` (SOPS-encrypted)
3. `DEFAULT_FROM_EMAIL` bijwerken naar een geldig afzenderadres
4. GlitchTip containers herstarten
5. Test-alert sturen om te verif ieren

### R7 — GitHub-verwijdering in offboarding (A.6.5)

**Huidige staat:** De `offboard_user()` functie in `admin.py` (regels 797-839) doet:
- Verwijdert groepslidmaatschappen (portal DB)
- Verwijdert productassignments (portal DB)
- Deactiveert gebruiker in Zitadel
- Zet status op "offboarded"

**Niet gedaan:** GitHub org membership wordt **niet** verwijderd. Een voormalig teamlid behoudt toegang tot alle GitHub-repositories.

**Marktstandaard:** Voor GitHub org member removal zijn er twee benaderingen:
1. **PyGithub** (populairst, 7k+ stars): `github.Organization.remove_from_membership(member)`. Vereist een GitHub PAT met `admin:org` scope.
2. **Directe httpx-calls** naar `DELETE /orgs/{org}/members/{username}`: simpeler, geen extra dependency, past bij de bestaande `httpx`-pattern in `zitadel.py`.

**Aanbeveling:** Directe httpx-call, consistent met de bestaande Zitadel-service pattern. Vereist:
- GitHub PAT met `admin:org` scope in SOPS
- Username mapping: Zitadel user_id -> GitHub username (nieuw veld in PortalUser of via Zitadel profile metadata)
- Graceful failure: GitHub-verwijdering mag offboarding niet blokkeren (log warning bij falen)

### R8 — backup-policy.md corrigeren (A.8.13)

**Huidige staat:** Meerdere feitelijke onjuistheden in het beleidsdocument:

1. **Encryptiemethode:** Policy zegt "Hetzner volume encryption" (regels 47-48, 53-54). Werkelijkheid: backups worden versleuteld met `age` (ChaCha20-Poly1305) naar twee AGE-recipients (zie `backup.sh` regels 117-125). Hetzner volume encryption bestaat ook, maar de primaire encryptie is age.

2. **Qdrant:** Policy claimt "Weekly snapshot, 2-week retention" (regel 48). `backup.sh` bevat **geen** Qdrant-backup — dit is intentioneel: Qdrant is een derived index die herbouwd kan worden vanuit Gitea bronbestanden. De policy moet dit expliciet vermelden.

3. **VictoriaLogs:** Policy claimt "Weekly volume backup, 2 weeks retention" (regel 54). In werkelijkheid heeft VictoriaLogs ingebouwde 30-dagen retentie. Er is geen separaat backup-mechanisme.

4. **asset-register.md:** Herhaalt de onjuiste Qdrant-claim ("Weekly snapshot, 2-week retention").

### R9 — Verwerkingsregister aanleggen (A.5.34)

**Huidige staat:** AVG artikel 30 vereist een register van verwerkingsactiviteiten. Dit ontbreekt volledig. De SoA claimt A.5.34 als COVERED vanwege "GDPR compliance by design", maar een verwerkingsregister is een expliciet wettelijk vereiste.

**Marktstandaard:** De Autoriteit Persoonsgegevens (AP) biedt een sjabloon voor het verwerkingsregister. Het format is niet voorgeschreven — een Markdown-document, spreadsheet, of database zijn allemaal acceptabel. Vereiste velden per verwerking (Art. 30 lid 1):
- Naam en contactgegevens verwerkingsverantwoordelijke
- Verwerkingsdoeleinden
- Categorieen betrokkenen en persoonsgegevens
- Categorieen ontvangers
- Doorgifte aan derde landen (n.v.t. voor Klai — EU-only)
- Bewaartermijnen
- Technische en organisatorische beveiligingsmaatregelen

**Aanpak:** Markdown-document in `klai-private/compliance/policies/processing-register.md`, consistent met de bestaande beleidsstructuur.

### R10 — Procedure eerste externe aanstelling (A.6.1)

**Huidige staat:** `personnel-screening.md` regel 49: "When the first non-founder team member joins, formal screening must be implemented." Er is geen concrete procedure beschreven voor deze overgang — wie is verantwoordelijk, welke stappen, welke checklist?

**Aanpak:** Sectie toevoegen aan `personnel-screening.md` met een concrete transitiechecklist.

### R11 — MFA-beleid in acceptable-use.md (A.5.10/A.8.5)

**Huidige staat:** De `mfa_policy` staat als veld in `PortalOrg` model (zie `OrgSettingsOut` in admin.py regel 108: `mfa_policy: Literal["optional", "recommended", "required"]`). De acceptable-use policy en endpoint-security policy bevatten wel de eis "All Klai accounts must use 2FA/MFA" (endpoint-security.md regel 81), maar:
- Geen definitie van wat "acceptabele MFA" is (TOTP? FIDO2? SMS?)
- Geen guidance over het `mfa_policy` organisatie-veld
- MFA-eis staat niet in de AUP zelf

**Aanpak:** MFA-sectie toevoegen aan `acceptable-use.md` met definities en koppeling naar het `mfa_policy` veld.

### R12 — SOPS sleutelrevocatieprocedure (A.8.2)

**Huidige staat:** Geen procedure gedocumenteerd voor het roteren van SOPS age-sleutels wanneer een teamlid vertrekt. Dit is kritiek: als een voormalig teamlid nog een age-key heeft, kan die persoon alle SOPS-encrypted secrets ontsleutelen.

**Aanpak:** Procedure documenteren in een nieuwe sectie van een bestaand beleidsdocument (of apart document). Stappen: genereer nieuwe key, update `.sops.yaml`, herversleutel alle bestanden, verwijder oude public key.

### R13 — Restoration testing procedure + eerste test (A.8.13)

**Huidige staat:** `backup-policy.md` definieert kwartaalse MongoDB-hersteltest (regels 157-161), maar er is nooit een test uitgevoerd en er zijn geen testresultaten gedocumenteerd.

**Aanpak:** Eerste hersteltest uitvoeren en documenteren. Dit is een operationele taak, geen code-wijziging.

### R14 — Kleine correcties

1. **backup.sh regel 154:** Commentaar zegt "keep last 7 days" (`Lokale cleanup: backups ouder dan 7 dagen verwijderen...`) maar de code `head -n -30` houdt de laatste 30 directories. Commentaar corrigeren naar "30 dagen".

2. **SoA entry A.7.7:** Status upgraden van PARTIAL naar COVERED. Het beleid staat beschreven in `endpoint-security.md` sectie "Clear Desk and Clear Screen" (regels 106-111).

---

## Requirements (EARS-format)

### Groep 1 — CI/CD Security

**REQ-SEC-002-01: Container image scanning**
WHEN een Docker image wordt gebouwd in de portal-api CI-pipeline, THEN voert het systeem een Trivy vulnerability scan uit op het gebouwde image met severity-filter `CRITICAL,HIGH` en unfixed vulnerabilities geignoreerd.

**REQ-SEC-002-02: Scan resultaat blokkering**
IF de Trivy scan CRITICAL of HIGH vulnerabilities vindt die niet in een `.trivyignore`-bestand staan, THEN blokkeert de CI-pipeline de deploy-stap (exit code 1).

**REQ-SEC-002-03: SARIF upload**
WHEN de Trivy scan voltooid is, THEN uploadt de CI-pipeline het SARIF-rapport naar GitHub Code Scanning zodat findings zichtbaar zijn in de Security-tab.

**REQ-SEC-002-04: SAST scanning**
WHEN code wordt gepusht naar de main branch van portal (backend of frontend), THEN voert de CI-pipeline een Semgrep SAST-scan uit met `--config auto` en `--config p/owasp-top-ten` configuratie.

**REQ-SEC-002-05: SAST resultaat blokkering**
IF Semgrep findings met severity ERROR detecteert, THEN blokkeert de CI-pipeline de verdere stappen (exit code 1).

**REQ-SEC-002-06: CVE-uitzondering documentatie**
Het systeem documenteert elke `--ignore-vuln` CVE-uitzondering in de CI-pipeline met:
- Een verwijzing naar een GitHub Issue in het workflow-commentaar
- Een entry in `vulnerability-management.md` met reden, einddatum, en verantwoordelijke

**REQ-SEC-002-07: Branch protection**
Het GitHub-repository heeft branch protection op `main` met:
- Minimaal 1 PR review vereist
- Status checks moeten passen (quality job)
- Force-push geblokkeerd
- Deletion van main geblokkeerd

### Groep 2 — Infrastructuur

**REQ-SEC-002-08: NTP-synchronisatie**
WHEN `setup.sh` wordt uitgevoerd op een nieuwe server, THEN activeert het script NTP-synchronisatie via `timedatectl set-ntp true` en verifieert dat `systemd-timesyncd` actief is.

**REQ-SEC-002-09: NTP-verificatie**
Het systeem logt het resultaat van `timedatectl timesync-status` als bewijs van tijdsynchronisatie na uitvoering van `setup.sh`.

**REQ-SEC-002-10: GlitchTip SMTP-alerting**
WHILE GlitchTip in productie draait, THEN stuurt het systeem alert-notificaties via SMTP naar een extern e-mailadres (niet `consolemail://`).

**REQ-SEC-002-11: GlitchTip SMTP-configuratie**
WHEN de GlitchTip deployment wordt geconfigureerd, THEN bevat de `EMAIL_URL` een geldig SMTP-endpoint en is `DEFAULT_FROM_EMAIL` ingesteld op een geldig afzenderadres.

### Groep 3 — Applicatie

**REQ-SEC-002-12: GitHub offboarding**
WHEN een gebruiker wordt ge-offboard via `offboard_user()`, THEN verwijdert het systeem ook het GitHub org-membership van de gebruiker via de GitHub API.

**REQ-SEC-002-13: GitHub offboarding foutafhandeling**
IF de GitHub API-call faalt tijdens offboarding, THEN logt het systeem een warning maar blokkeert de rest van het offboarding-proces niet (graceful degradation).

**REQ-SEC-002-14: GitHub username mapping**
Het systeem slaat het GitHub-username op als optioneel veld in de PortalUser-tabel, zodat offboarding het juiste GitHub-account kan identificeren.

### Groep 4 — Beleidsdocumenten

**REQ-SEC-002-15: Backup-policy encryptiecorrectie**
Het `backup-policy.md` document vermeldt `age` (ChaCha20-Poly1305) als de primaire encryptiemethode voor backups, in lijn met de werkelijke implementatie in `backup.sh`.

**REQ-SEC-002-16: Backup-policy Qdrant-correctie**
Het `backup-policy.md` document vermeldt dat Qdrant een derived index is die bewust niet wordt gebackupt, met een notitie dat herbouw mogelijk is vanuit bronbestanden in Gitea.

**REQ-SEC-002-17: Backup-policy VictoriaLogs-correctie**
Het `backup-policy.md` document vermeldt dat VictoriaLogs 30-dagen ingebouwde retentie heeft en geen separaat backup-mechanisme vereist.

**REQ-SEC-002-18: Asset-register Qdrant-correctie**
Het `asset-register.md` document corrigeert de Qdrant-entry: de backup-kolom vermeldt "Niet gebackupt (derived index, herbouwbaar vanuit Gitea)" in plaats van "Weekly snapshot, 2-week retention".

**REQ-SEC-002-19: Verwerkingsregister**
Het systeem bevat een AVG Art. 30 verwerkingsregister (`processing-register.md`) met minimaal de volgende verwerkingsactiviteiten:
- Klantauthenticatie en -autorisatie (Zitadel)
- AI-chatgesprekken per tenant (MongoDB/LibreChat)
- Documentverwerking en vectorisatie (Gitea/Qdrant)
- Platform-monitoring en foutopsporing (VictoriaLogs/GlitchTip)
- Website-analytics (Umami)
- Facturatie en klantenrelatie (Moneybird)

**REQ-SEC-002-20: Eerste externe aanstelling procedure**
Het `personnel-screening.md` document bevat een concrete transitiechecklist voor de eerste niet-oprichter aanstelling, met verantwoordelijkheden, tijdlijn, en vereiste documenten.

**REQ-SEC-002-21: MFA-beleid**
Het `acceptable-use.md` document bevat een MFA-sectie die definieert:
- Welke MFA-methoden acceptabel zijn (TOTP, FIDO2/WebAuthn)
- Welke methoden NIET acceptabel zijn (SMS)
- Hoe het `mfa_policy` organisatie-veld zich verhoudt tot individuele MFA-vereisten

**REQ-SEC-002-22: SOPS sleutelrevocatie**
Het systeem bevat een gedocumenteerde SOPS-sleutelrevocatieprocedure met stappen voor:
- Genereren van een nieuwe age-key voor de vervangende persoon
- Bijwerken van `.sops.yaml` met de nieuwe public key en verwijdering van de oude
- Herversleutelen van alle SOPS-bestanden met de nieuwe keyring
- Verwijderen van de oude public key uit alle configuraties

**REQ-SEC-002-23: Eerste hersteltestresultaat**
Het systeem bevat gedocumenteerde testresultaten van een MongoDB-hersteltest, inclusief:
- Datum en tijdstip van de test
- Gebruikte backup (datum/versie)
- Herstelduur
- Verificatieresultaten (pass/fail met details)
- Eventuele problemen en oplossingen

**REQ-SEC-002-24: backup.sh commentaarcorrectie**
Het commentaar in `backup.sh` bij de lokale cleanup-sectie (~regel 154) vermeldt "30 dagen" in plaats van "7 dagen", consistent met de code `head -n -30`.

**REQ-SEC-002-25: SoA A.7.7 statuscorrectie**
De SoA-entry voor A.7.7 (Clear desk and clear screen) heeft status COVERED met referentie naar `endpoint-security.md` sectie "Clear Desk and Clear Screen".

---

## Acceptatiecriteria per requirement

### REQ-SEC-002-01 t/m 03: Container scanning

```gherkin
Given een push naar main die portal/backend/ bestanden wijzigt
When de portal-api CI-pipeline draait
Then bevat de pipeline een "Container image scan" stap met Trivy
And de stap scant het gebouwde ghcr.io/getklai/portal-api image
And de stap filtert op severity CRITICAL en HIGH
And de stap negeert unfixed vulnerabilities
And bij gevonden CRITICAL/HIGH vulnerabilities faalt de pipeline (exit code 1)
And het SARIF-rapport is zichtbaar in de GitHub Security tab
```

### REQ-SEC-002-04 t/m 05: SAST

```gherkin
Given een push naar main die portal/backend/ of portal/frontend/ bestanden wijzigt
When de CI-pipeline draait
Then bevat de pipeline een "SAST scan" stap met Semgrep
And Semgrep draait met --config auto en --config p/owasp-top-ten
And bij ERROR-severity findings faalt de pipeline
And findings zijn zichtbaar in de CI-logs
```

### REQ-SEC-002-06: CVE-uitzondering

```gherkin
Given dat CVE-2026-4539 in de pipeline wordt geignoreerd
When een engineer de pipeline-configuratie bekijkt
Then staat er een commentaar met een GitHub Issue-nummer bij de --ignore-vuln regel
And het GitHub Issue bevat: reden, compenserende controle, herbeoordelingsdatum
And vulnerability-management.md bevat een entry voor deze CVE
```

### REQ-SEC-002-07: Branch protection

```gherkin
Given de GitHub repository GetKlai/klai
When een gebruiker probeert direct naar main te pushen (zonder PR)
Then wordt de push geweigerd
And bij het aanmaken van een PR is minimaal 1 review vereist
And de quality job moet passen voordat merge mogelijk is
And force-push naar main is geblokkeerd
```

### REQ-SEC-002-08 t/m 09: NTP

```gherkin
Given een nieuw geinstalleerde server
When setup.sh wordt uitgevoerd
Then is systemd-timesyncd actief (timedatectl show | grep NTP=yes)
And toont timedatectl timesync-status een gesynchroniseerde klok
And is de output gelogd in de setup-output
```

### REQ-SEC-002-10 t/m 11: GlitchTip SMTP

```gherkin
Given een GlitchTip-installatie in productie
When een error-alert wordt getriggerd in GlitchTip
Then ontvangt het geconfigureerde e-mailadres een notificatie
And de EMAIL_URL in docker-compose bevat geen "consolemail://"
And DEFAULT_FROM_EMAIL is ingesteld op een geldig @getklai.com adres
```

### REQ-SEC-002-12 t/m 14: GitHub offboarding

```gherkin
Given een gebruiker met github_username "janssen" in de PortalUser-tabel
When een admin offboard_user() aanroept voor deze gebruiker
Then wordt de gebruiker verwijderd uit de GitHub-organisatie
And als de GitHub API faalt wordt een warning gelogd
And de rest van het offboarding-proces gaat door
And de offboarding-response is succesvol (geen 5xx)

Given een gebruiker zonder github_username in de PortalUser-tabel
When een admin offboard_user() aanroept voor deze gebruiker
Then wordt de GitHub-verwijderingsstap overgeslagen
And wordt een info-logmelding geschreven dat er geen GitHub-account gekoppeld is
```

### REQ-SEC-002-15 t/m 18: Backup-policy en asset-register correcties

```gherkin
Given het document backup-policy.md
When een auditor de encryptiemethode controleert
Then vermeldt het document "age (ChaCha20-Poly1305)" als primaire encryptie
And is "Hetzner volume encryption" niet meer de primaire vermelde methode

Given het document backup-policy.md
When een auditor de Qdrant-backup controleert
Then vermeldt het document dat Qdrant bewust niet wordt gebackupt
And is de reden vermeld: "derived index, herbouwbaar vanuit Gitea bronbestanden"

Given het document backup-policy.md
When een auditor VictoriaLogs backup controleert
Then vermeldt het document "ingebouwde 30-dagen retentie, geen separaat backup"

Given het document asset-register.md
When een auditor de Qdrant-entry controleert
Then staat er "Niet gebackupt (derived index)" in de backup-kolom
```

### REQ-SEC-002-19: Verwerkingsregister

```gherkin
Given het document processing-register.md
When een auditor of AP-toezichthouder het register opvraagt
Then bevat het register minimaal 6 verwerkingsactiviteiten
And bevat elke entry: doeleinde, categorieen betrokkenen, persoonsgegevens, ontvangers, bewaartermijn, beveiligingsmaatregelen
And is er geen vermelding van doorgifte naar derde landen buiten de EU
And zijn de contactgegevens van de verwerkingsverantwoordelijke opgenomen
```

### REQ-SEC-002-20: Eerste externe aanstelling

```gherkin
Given het document personnel-screening.md
When het team zich voorbereidt op de eerste niet-oprichter aanstelling
Then bevat het document een checklist met minimaal:
  - Wie verantwoordelijk is voor activering van formele screening
  - Welke documenten klaar moeten zijn (NDA, VOG-aanvraag, AUP-ondertekening)
  - Tijdlijn: wanneer welke stap uitgevoerd moet zijn t.o.v. startdatum
  - Hoe de compenserende controles voor het oprichtersteam worden uitgefaseerd
```

### REQ-SEC-002-21: MFA-beleid

```gherkin
Given het document acceptable-use.md
When een teamlid of auditor het MFA-beleid opzoekt
Then bevat het document een sectie "Multi-Factor Authentication"
And vermeldt het dat TOTP en FIDO2/WebAuthn acceptabele methoden zijn
And vermeldt het dat SMS-gebaseerde MFA niet acceptabel is
And legt het de relatie uit tussen het mfa_policy org-veld en individuele vereisten
```

### REQ-SEC-002-22: SOPS sleutelrevocatie

```gherkin
Given een gedocumenteerde SOPS-sleutelrevocatieprocedure
When een teamlid het team verlaat
Then beschrijft de procedure stap-voor-stap hoe:
  1. Een nieuwe age-key wordt gegenereerd (als vervanging)
  2. .sops.yaml wordt bijgewerkt
  3. Alle SOPS-bestanden worden herversleuteld
  4. De oude public key wordt verwijderd
And bevat de procedure een verificatiestap na herversleuteling
And is duidelijk wie verantwoordelijk is voor elke stap
```

### REQ-SEC-002-23: Eerste hersteltest

```gherkin
Given de backup-policy die kwartaalse MongoDB-hersteltests vereist
When de eerste hersteltest wordt uitgevoerd
Then zijn de resultaten gedocumenteerd met:
  - Datum en tijdstip
  - Backup-bestand dat is hersteld
  - Herstelduur in minuten
  - Documentcount-verificatie (bron vs. hersteld)
  - Sample query-resultaten
  - Verdict: PASS of FAIL met toelichting
```

### REQ-SEC-002-24: backup.sh commentaarcorrectie

```gherkin
Given het bestand deploy/scripts/backup.sh
When een engineer de lokale cleanup-sectie leest
Then zegt het commentaar "30 dagen" (niet "7 dagen")
And is het consistent met de code `head -n -30`
```

### REQ-SEC-002-25: SoA A.7.7 correctie

```gherkin
Given het document iso27001-soa.md
When een auditor A.7.7 controleert
Then is de status COVERED (niet PARTIAL)
And is er een referentie naar endpoint-security.md sectie "Clear Desk and Clear Screen"
```

---

## Implementatieplan

### Prioriteit 1 — Snelle wins (geen of minimale code)

| Item | Type | Geschatte complexiteit | Betrokken bestanden |
|---|---|---|---|
| R14 backup.sh commentaar | Fix | Triviaal | `deploy/scripts/backup.sh` |
| R14 SoA A.7.7 status | Fix | Triviaal | `klai-private/compliance/iso27001-soa.md` |
| R4 Branch protection | Config | Laag | GitHub API (geen code) |
| R3 CVE-documentatie | Docs | Laag | `.github/workflows/portal-api.yml`, `vulnerability-management.md` |
| R8 Backup-policy correcties | Docs | Laag | `backup-policy.md`, `asset-register.md` |

### Prioriteit 2 — CI/CD uitbreiding

| Item | Type | Geschatte complexiteit | Betrokken bestanden |
|---|---|---|---|
| R1 Trivy container scanning | CI | Medium | `.github/workflows/portal-api.yml` |
| R2 Semgrep SAST | CI | Medium | `.github/workflows/portal-api.yml` (of nieuwe workflow) |
| R5 NTP in setup.sh | Infra | Laag | `deploy/setup.sh` |

### Prioriteit 3 — Infrastructuur en applicatie

| Item | Type | Geschatte complexiteit | Betrokken bestanden |
|---|---|---|---|
| R6 GlitchTip SMTP | Infra/Config | Medium | `deploy/docker-compose.yml`, `.env.sops` |
| R7 GitHub offboarding | Code | Medium-Hoog | `admin.py`, Alembic migratie, `zitadel.py` of nieuw `github.py` |

### Prioriteit 4 — Beleidsdocumenten

| Item | Type | Geschatte complexiteit | Betrokken bestanden |
|---|---|---|---|
| R9 Verwerkingsregister | Docs | Medium | Nieuw: `processing-register.md` |
| R10 Eerste hire procedure | Docs | Laag | `personnel-screening.md` |
| R11 MFA-beleid | Docs | Laag | `acceptable-use.md` |
| R12 SOPS revocatie | Docs | Laag | Nieuw sectie of document |
| R13 Eerste hersteltest | Ops | Medium | Testuitvoering + rapportage |

### Technische notities

**R1 — Trivy integratie in portal-api.yml:**
```yaml
# Toevoegen NA de build-push stap, VOOR deploy
- name: Run Trivy vulnerability scanner
  uses: aquasecurity/trivy-action@0.33.1
  with:
    image-ref: ghcr.io/getklai/portal-api:${{ github.sha }}
    format: 'sarif'
    output: 'trivy-results.sarif'
    severity: 'CRITICAL,HIGH'
    ignore-unfixed: true
    exit-code: '1'

- name: Upload Trivy SARIF
  uses: github/codeql-action/upload-sarif@v3
  if: always()
  with:
    sarif_file: 'trivy-results.sarif'
```

**R2 — Semgrep integratie:**
```yaml
# Nieuwe job in portal-api.yml OF aparte workflow
semgrep:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - name: Semgrep SAST scan
      uses: semgrep/semgrep-action@v1
      with:
        config: >-
          auto
          p/owasp-top-ten
      env:
        SEMGREP_APP_TOKEN: ""  # geen account nodig voor OSS
```

**R5 — NTP in setup.sh:**
```bash
# Toe te voegen na de timezone-instelling in sectie [2/7]
echo "Enabling NTP synchronization..."
timedatectl set-ntp true
sleep 2
timedatectl timesync-status
```

**R7 — GitHub offboarding architectuur:**
- Nieuw veld: `github_username: Mapped[str | None]` in `PortalUser` model
- Alembic migratie voor de kolom
- Nieuwe functie in service-laag: `async def remove_github_member(org: str, username: str) -> bool`
- Aanroep in `offboard_user()` met try/except rond de GitHub-call
- GitHub PAT als SOPS-secret: `GITHUB_ADMIN_PAT`

**R6 — GlitchTip SMTP:**
- EMAIL_URL format: `smtp://user:password@smtp.cloud86.io:587/?tls=True`
- SOPS-encrypted in `.env.sops`
- `DEFAULT_FROM_EMAIL`: `errors@getklai.com`
- Na configuratie: test via GlitchTip admin panel -> "Send Test Notification"

---

## Risico's en mitigatie

| Risico | Impact | Mitigatie |
|---|---|---|
| Trivy false positives blokkeren deploys | Hoog | `.trivyignore` bestand met gedocumenteerde uitzonderingen; `ignore-unfixed: true` |
| Semgrep false positives | Medium | Start met `--severity ERROR` only; verfijn later naar WARNING |
| GitHub PAT security | Hoog | PAT in SOPS; minimale scope (`admin:org`); regelmatige rotatie |
| SMTP-credentials in compose | Medium | Alleen via SOPS `.env.sops`; nooit in cleartext |
| Hersteltest verstoort productie | Hoog | Test op separaat backup volume; nooit op productie-database |

---

## Traceerbaarheid

| Requirement | SOA Control | Huidige status | Doelstatus |
|---|---|---|---|
| REQ-SEC-002-01..03 | A.8.7 | FALSE | COVERED |
| REQ-SEC-002-04..05 | A.8.29 | PARTIAL | COVERED |
| REQ-SEC-002-06 | A.8.8 | PARTIAL | COVERED |
| REQ-SEC-002-07 | A.8.32 | COVERED (niet aantoonbaar) | COVERED (aantoonbaar) |
| REQ-SEC-002-08..09 | A.8.17 | FALSE (claim onjuist) | COVERED (bewezen) |
| REQ-SEC-002-10..11 | A.8.16 | PARTIAL | COVERED |
| REQ-SEC-002-12..14 | A.6.5 | FALSE (GitHub gap) | COVERED |
| REQ-SEC-002-15..18 | A.8.13 | PARTIAL (onjuiste docs) | PARTIAL (correcte docs) |
| REQ-SEC-002-19 | A.5.34 | FALSE (register ontbreekt) | PARTIAL -> COVERED |
| REQ-SEC-002-20 | A.6.1 | PARTIAL | COVERED |
| REQ-SEC-002-21 | A.5.10/A.8.5 | PARTIAL | COVERED |
| REQ-SEC-002-22 | A.8.2 | PARTIAL (niet gedocumenteerd) | COVERED |
| REQ-SEC-002-23 | A.8.13 | PARTIAL (geen test) | COVERED |
| REQ-SEC-002-24..25 | A.8.13/A.7.7 | Incorrecte vermelding | Correcte vermelding |
