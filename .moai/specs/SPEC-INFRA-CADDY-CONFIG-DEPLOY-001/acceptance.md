# SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 — Acceptance Criteria

Zeven Given/When/Then scenarios. Elke AC is mechanisch verifieerbaar
via tool-output, exit-codes, en file-comparison.

## AC-1 — Caddyfile sync vindt plaats bij main-merge

- **Given** R1 is uitgevoerd (deploy-compose.yml uitgebreid met
  `deploy/caddy/Caddyfile` paths-trigger + sparse-checkout + rsync
  blok),
- **When** een commit naar `main` `deploy/caddy/Caddyfile` wijzigt,
- **Then** `gh run list --workflow deploy-compose.yml -L 1 --json
  conclusion --jq '.[0].conclusion'` returns `"success"` binnen 5 minuten,
  EN `ssh core-01 "sha256sum /opt/klai/caddy/Caddyfile"` matched
  `git -C ~/Developer/Klai show HEAD:deploy/caddy/Caddyfile | sha256sum`.

**Test:** trigger een no-op comment-edit op `deploy/caddy/Caddyfile`
post-merge, runt de workflow, vergelijk hashes.

## AC-2 — Container leest gesyncte Caddyfile

- **Given** AC-1 hold,
- **When** `ssh core-01 "docker exec klai-core-caddy-1 sha256sum
  /etc/caddy/Caddyfile"` draait,
- **Then** de hash matched die uit AC-1 (host file).

**Test:** zelfde no-op comment-PR; verifieer dat de comment ook
binnen de container zichtbaar is.

## AC-3 — Image rebuild blijft uitsluitend op binary-changes triggeren

- **Given** R2 is uitgevoerd (caddy.yml paths reduced),
- **When** een PR alléén `deploy/caddy/Caddyfile` wijzigt,
- **Then** `gh pr checks <pr>` listet `caddy / build-push` NIET als
  een check-run; alleen `Validate Caddyfile / validate` (R3) verschijnt.

**Test:** maak een PR met enkel een Caddyfile comment-toevoeging;
inspect `gh pr checks`.

## AC-4 — Pre-merge syntax validatie blokkeert kapotte Caddyfile

- **Given** R3 is uitgevoerd (`caddy-validate.yml` bestaat),
- **When** een PR een syntactisch ongeldige regel toevoegt aan
  `deploy/caddy/Caddyfile` (bijv. `not_a_directive { broken }`),
- **Then** `caddy-validate.yml` faalt met exit-code 1 en de PR-status
  toont `Validate Caddyfile / validate` als `failed`. De PR kan niet
  gemerged worden zonder admin override.

**Test:** experimentele PR met geforceerde syntax-fout; assertie via
`gh pr checks` op `failure` status.

## AC-5 — Geldige Caddyfile passeert pre-merge validate

- **Given** R3 is uitgevoerd,
- **When** een PR een geldige wijziging toevoegt (bijv. extra header
  in een handle-block),
- **Then** `caddy-validate.yml` exit-code 0; PR-check `success`.

**Test:** PR met deze SPEC zelf (Caddyfile is ongewijzigd, validate
moet passeren).

## AC-6 — Post-recreate health check vangt failures

- **Given** R4 is uitgevoerd,
- **When** een hypothetische Caddyfile change leidt tot een container
  die niet binnen 10s `running` is (bijv. een runtime-error die de
  validate niet vangt),
- **Then** de `deploy-compose.yml` workflow faalt met exit-code 1
  en de log dump bevat de laatste 50 caddy-regels via
  `docker compose ... logs --tail 50 caddy`.

**Test:** lokaal handmatig met een gefabriceerde fault. Niet in CI
eisbaar — verificatie van het code-pad in de YAML-diff is voldoende
voor merge.

## AC-7 — Tenant Caddyfile mechanisme blijft ongewijzigd

- **Given** R5 is gerespecteerd (geen wijzigingen aan
  `_write_tenant_caddyfile`, `_reload_caddy`, of de `caddy-tenants`
  volume mounts),
- **When** een nieuwe tenant geprovisioned wordt na deze SPEC's merge
  (test-fixture in dev-stack),
- **Then** alle bestaande tests groen blijven:
  - `tests/services/provisioning/test_infrastructure.py
    ::test_writes_caddyfile_with_correct_content`
  - `tests/services/provisioning/test_infrastructure.py
    ::test_restarts_caddy_container`
  - `tests/services/provisioning/test_orchestrator.py
    ::test_caddy_lock_exists`
  - `tests/services/provisioning/test_orchestrator.py
    ::test_caddy_lock_is_module_level_singleton`
  - `tests/services/provisioning/test_orchestrator.py
    ::test_compensate_caddy_is_noop_when_not_written`

**Test:** `cd klai-portal/backend && uv run pytest -k "caddy"` runt
groen op de feature branch (geen testfile changed).

## Verification commands (post-merge)

```bash
# AC-1: workflow groen
gh run list --workflow deploy-compose.yml -L 1 \
  --json conclusion --jq '.[0].conclusion'
# expect: "success"

# AC-1+2: file synced + container sees it
LOCAL=$(git show main:deploy/caddy/Caddyfile | sha256sum | awk '{print $1}')
HOST=$(ssh core-01 "sha256sum /opt/klai/caddy/Caddyfile" | awk '{print $1}')
CTR=$(ssh core-01 "docker exec klai-core-caddy-1 sha256sum /etc/caddy/Caddyfile" | awk '{print $1}')
[ "$LOCAL" = "$HOST" ] && [ "$HOST" = "$CTR" ] && echo "PASS" || echo "FAIL"

# AC-3: caddy.yml didn't run on Caddyfile-only PR
gh pr checks <pr-number> --json name --jq '.[].name' | grep -c "caddy / build-push"
# expect: 0

# AC-7: provisioning tests still green
cd klai-portal/backend && uv run pytest -k "caddy" -v
```
