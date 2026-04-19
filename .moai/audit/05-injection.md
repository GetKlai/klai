# Fase 4 — Injection / SAST Scan

**Datum:** 2026-04-19
**Tools:** `bandit` (MEDIUM+ severity/confidence), `semgrep` (p/owasp-top-ten + p/python + p/typescript + p/javascript + p/react + p/xss + r/python.*)
**Scope:** 9 Python services + 3 frontend projects (portal-frontend, widget, website)
**Raw output:** `.moai/audit/fase4-raw/`

## TL;DR

**Opvallend weinig critical injection-bugs gevonden.** Klai-codebase is in goede staat qua injection-surface. Dat komt doordat:
- SQLAlchemy ORM + `text()` met parameterized queries wordt consistent gebruikt (geen string-concat SQL)
- Input validation via Pydantic v2 op alle API boundaries
- `hmac.compare_digest` wordt consistent gebruikt voor secret compares (zie SEC-004/007/008 fixes)
- Frontend gebruikt React JSX (auto-escaping); geen `dangerouslySetInnerHTML` of `innerHTML` gevonden

**Totaal findings:** 5 genuine items + 1 false positive + 134 lint-noise. 0 SQL-injection, 0 XSS, 0 command-injection in productie-code.

## Findings per severity

| ID | Severity | Service | Finding |
|---|---|---|---|
| **F-023** | MEDIUM | klai-scribe/whisper-server | `tempfile.mktemp()` — unsafe + deprecated |
| **F-024** | MEDIUM | klai-connector + klai-knowledge-mcp | `host="0.0.0.0"` bind — intentional, already noqa'd in mcp |
| **F-025** | MEDIUM | klai-portal/backend | Raw SQL in Alembic migrations met f-string (`B608`) — false positive (migrations zijn static, geen user input) |
| ~~F-026~~ | FALSE POSITIVE | klai-portal/backend | Regex DoS — verified safe via live DoS-test (<0.02ms op 300+ char adversarial) |
| **F-027** | LOW | klai-portal + knowledge-ingest | Missing `encoding="utf-8"` in `open()` calls (5 locations) |
| **F-028** | LOW | klai-portal/backend | Leftover `time.sleep()` in `provisioning/infrastructure.py:138` |
| **F-029** | INFO | klai-knowledge-ingest | Dockerfile mist `USER` directive (runs as root) |

---

### F-023 — Insecure `tempfile.mktemp()` in whisper-server [MEDIUM]

**Locatie:** `klai-scribe/whisper-server/main.py:61`

**Bewijs:**
```python
silence = np.zeros(16000, dtype=np.float32)
tmp = Path(tempfile.mktemp(suffix=".wav"))
```

**Probleem:** `tempfile.mktemp()` is officieel deprecated (Python docs: "THIS FUNCTION IS UNSAFE"). TOCTOU race mogelijk — een attacker kan na naam-generatie maar vóór file-open het pad symlinken naar een andere file.

**Impact:** Beperkt. Whisper-server draait in Docker met single user, beperkte filesystem-access, geen multi-user. Maar toekomstige refactor kan het exposen.

**Mitigerend:** Container-isolatie. Whisper-server is klai-net-intern, geen user file-uploads naar deze specifieke tempfile.

**Aanbeveling:** Vervang door `tempfile.NamedTemporaryFile(suffix=".wav", delete=False)` of context manager. 5-regel fix.

**Nota:** Whisper-server is deel van scribe, dat momenteel in rebuild. Fix in scribe-rebuild meenemen ipv. aparte SPEC.

---

### F-024 — `0.0.0.0` bind in klai-connector + knowledge-mcp [MEDIUM / INFO]

**Locaties:**
- `klai-connector/app/main.py:164` — `uvicorn.run(..., host="0.0.0.0", port=8200)`
- `klai-knowledge-mcp/main.py:443` — `uvicorn.run(..., host="0.0.0.0", ...)  # noqa: S104` (al genoteerd)

**Context:** Binden op `0.0.0.0` is intentional voor Docker containers — de Docker port-mapping regelt externe bereikbaarheid. Bandit signaleert dit generiek.

**Acceptabel:** Services binden in containers, network-isolatie via Docker. Caddy/DOCKER-USER iptables regelen welke services extern bereikbaar zijn.

**Aanbeveling:** Voeg `# noqa: S104` toe aan `klai-connector/app/main.py:164` voor consistency met knowledge-mcp. Geen functional fix nodig.

---

### F-025 — Raw SQL f-string in Alembic RLS migrations [MEDIUM — FALSE POSITIVE]

**Locaties:**
- `klai-portal/backend/alembic/versions/c5d6e7f8a9b0_add_rls_policies.py:50, 56`
- `klai-portal/backend/alembic/versions/e669581d441f_add_rls_phase2_safe_tables.py:53`

**Bewijs:**
```python
op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
    f"CREATE POLICY tenant_isolation ON portal_group_membership ..."
)
```

**Analyse:** f-string is gebruikt voor DDL (policy creation per table). Geen user input in de string — alle waarden zijn compile-time constants (table names uit een list). Dit is een FALSE POSITIVE.

**Aanbeveling:** Voeg `# noqa: B608` toe aan de drie locaties (consistency met bestaande `# nosemgrep`).

---

### F-026 — Regex DoS in domain validation [FALSE POSITIVE]

**Locatie:** `klai-portal/backend/app/services/domain_validation.py:21`

**Regex:** `^(?!-)[a-z0-9-]{1,63}(?<!-)(\.[a-z0-9-]{1,63})*\.[a-z]{2,}$`

**Status: VERIFIED SAFE.** Live test op 7 adversarial inputs (300+ chars, repeated hyphens, many dots, many labels):
```
  0.01ms  match     len=18   normal.example.com
  0.00ms  match     len=64   a-a-a-a-a... (repeated 30x)
  0.00ms  no-match  len=124  (too long for single label)
  0.00ms  match     len=317  multi-label adversarial
  0.01ms  match     len=123  many-short-labels
```

Alle matches < 0.02ms op 300+ char input. Geen backtracking bomb.

**Waarom semgrep false-flagged:** Rule `regex_dos` is heuristisch — detecteert `(...)*` patterns generisch, ook wanneer inner content bounded is. De `{1,63}` upper bound voorkomt exponential blow-up.

**Aanbeveling:** Voeg `# nosemgrep: regex_dos` toe op regel 21 met korte comment dat de regex verified is. 1 regel.

---

### F-027 — Missing `encoding=` in `open()` [LOW]

**Locaties:**
- `klai-knowledge-ingest/knowledge_ingest/clustering.py:115, 175`
- `klai-portal/backend/app/api/mcp_servers.py:44`
- `klai-portal/backend/app/services/provisioning/generators.py:50, 59`

**Probleem:** Zonder `encoding=` valt Python terug op OS-default encoding (UTF-8 op Linux, maar CP-1252 op Windows dev-machines). Kan corruptie veroorzaken op bestanden met special characters.

**Impact:** Geen security-issue; wel correctness/portability. Relevant voor Windows dev-omgeving.

**Aanbeveling:** Bulk-fix via `ruff --select UP015 --fix` (ruff heeft deze rule). 5 locaties, ~5 regels.

---

### F-028 — Leftover `time.sleep()` in provisioning [LOW]

**Locatie:** `klai-portal/backend/app/services/provisioning/infrastructure.py:138`

**Probleem:** Semgrep vraagt "did you mean to leave this in?" — `time.sleep()` blokkeert async event loop. In async context zou `asyncio.sleep()` correct zijn.

**Impact:** Als dit in een async-handler loopt = blokkeert event loop, DoS-achtig voor andere requests.

**Aanbeveling:** Code-review de context — is dit sync code of async? Als async → `asyncio.sleep`. Als bewust sync voor provisioning worker → `# noqa` + comment.

---

### F-029 — Dockerfile mist `USER` directive [INFO]

**Locatie:** `klai-knowledge-ingest/Dockerfile:15`

**Probleem:** Container draait als root tenzij `USER` expliciet gezet. Attacker die in container inbreekt heeft root-rechten.

**Mitigerend:** Docker rootless mode kan dit op platform-niveau opvangen. Klai core-01 draait niet rootless.

**Aanbeveling:** Voeg `USER 1000` (of een named user) toe. Standaard CI-practice. Te bundelen met een platform-wide "containers run as non-root" pass — andere Dockerfiles hebben waarschijnlijk hetzelfde patroon.

**Vervolgactie:** Audit ALLE Dockerfiles in monorepo op `USER`-directive. Separate SPEC-SEC-015?

---

## Frontend scan — 0 findings

Semgrep (p/typescript + p/javascript + p/react + p/xss) op:
- `klai-portal/frontend/src`
- `klai-widget/src`
- `klai-website/src`

**Result:** 0 findings. React JSX auto-escaping is consistent gebruikt. Geen `dangerouslySetInnerHTML`, geen `innerHTML`, geen `eval`, geen `document.write`.

**Caveat:** Semgrep community-rules dekken niet alle XSS-sinks. Voor dieptescan zou `eslint-plugin-security` + `eslint-plugin-no-unsanitized` + SonarJS handig zijn. Niet kritiek gezien design.

---

## Lint-noise findings (134 semgrep-extended) — niet in fix-roadmap

| Count | Check | Analyse |
|---|---|---|
| 67 | `logging-error-without-handling` | `logger.error()` buiten except-block. Legit Python style in 90%+ cases. Not a bug. |
| 49 | `is-function-without-parentheses` | `assert X is truthy` patterns in test-code. Niet relevant. |
| 6 | `return-not-in-function` | Parser edge cases in type-stub files. |
| 5 | `unspecified-open-encoding` | Zie F-027. |
| 4 | `pass-body-fn` | Empty placeholder methods (`def x(): pass`). Design choice. |

**Conclusie:** Voor elke deze categorieën was signal-to-noise < 5%. Niet worth tracking per finding.

---

## Open items (nog niet gedekt door deze scan)

1. **CodeIndex impact-analyse op F-026**: wie roept `domain_validation` aan? Bepaalt echte blast radius.
2. **SSRF-patterns**: `httpx.AsyncClient` calls met user-controlled URLs — bijv. in `klai-connector/app/adapters/webcrawler.py`, `klai-knowledge-ingest/knowledge_ingest/crawl4ai_client.py`. Semgrep heeft `python.requests` rules gedraaid maar vond geen hits — wel aanbeveling: manual review van connector OAuth redirect handling.
3. **Deserialization**: pickle / yaml / json loads in production code — geen unsafe patterns gevonden.
4. **Path traversal**: file-upload in scribe + knowledge-ingest — semgrep dekt het niet compleet. Manual review recommended voor `POST /v1/transcribe` filename sanitization.
5. **Template injection**: Jinja2 in klai-mailer — zag geen `| safe` of `Markup()` misuse, maar verdient kort manual pass.

## SEC-mapping naar fix-roadmap

Alle Fase 4 findings toegevoegd aan `99-fix-roadmap.md`:

| Finding | Nieuwe SEC-ID | Prio |
|---|---|---|
| ~~F-026 regex DoS~~ | n/a | FALSE POSITIVE — add `# nosemgrep` comment only |
| F-023 tempfile.mktemp | in SPEC-VEXA-003 (scribe rebuild) | existing |
| F-025 Alembic f-string | SEC-016 | P3 cosmetic — add noqa |
| F-024 0.0.0.0 bind | SEC-016 | P3 cosmetic — add noqa |
| F-027 open() encoding | SEC-016 | P3 ruff bulk-fix |
| F-028 time.sleep | SEC-017 | P2 code-review context |
| F-029 Dockerfile USER | SEC-018 | P2 monorepo-wide audit |

## Verificatie-commands (herhalen Fase 4 later)

```bash
# Bandit per service
for svc in klai-*/; do
  uvx bandit -r "$svc" -x "*/.venv/*,*/tests/*" -ll -ii -f json
done

# Semgrep OWASP + python
uvx semgrep --config=p/owasp-top-ten --config=p/python \
  --exclude '.venv' --exclude 'tests' \
  klai-connector klai-focus/research-api klai-knowledge-ingest \
  klai-portal/backend klai-retrieval-api klai-scribe klai-mailer

# Semgrep frontend
uvx semgrep --config=p/typescript --config=p/react --config=p/xss \
  --exclude 'node_modules' --exclude 'dist' \
  klai-portal/frontend/src klai-widget/src klai-website/src
```

## Changelog

| Datum | Wijziging |
|---|---|
| 2026-04-19 | Initiele Fase 4 scan. Bandit + Semgrep op 9 Python services + 3 frontend projects. 7 genuine findings (F-023..F-029), 134 noise. F-026 regex DoS is HIGH, rest MEDIUM/LOW/INFO. |
