# SPEC-INFRA-CONFIG-SYNC-001 — Acceptance Criteria

Vijf Given/When/Then scenarios. Elke AC is mechanisch verifieerbaar.

## AC-1 — Helper-refactor preserves caddy behaviour

- **Given** R1 + R2 zijn uitgevoerd (helper geëxtract, caddy-call via
  helper),
- **When** een commit naar `main` `deploy/caddy/Caddyfile` wijzigt,
- **Then** de log toont `caddy config content changed:` of `caddy
  config unchanged; skipping recreate.` afhankelijk van content-diff.
  Bij content change: caddy wordt force-recreated en gaat binnen 10s
  terug naar `running`. Functionaliteit identiek aan SPEC-INFRA-CADDY-
  CONFIG-DEPLOY-001 — verificatie via 3-way sha256 match (zie AC-4).

**Test:** Caddyfile is in deze SPEC niet aangeraakt — eerste post-merge
run zal `caddy config unchanged; skipping recreate.` echoen. Bevestigt
de no-op pad. Een latere Caddyfile-edit in een aparte PR test het
content-change pad.

## AC-2 — Alloy config sync

- **Given** R2 + R3 uitgevoerd (alloy in helper-call set + paths +
  sparse-checkout),
- **When** een commit naar `main` `deploy/alloy/config.alloy` wijzigt,
- **Then** `gh run list --workflow deploy-compose.yml -L 1 --json
  conclusion --jq '.[0].conclusion'` returns `"success"` binnen 5
  minuten, EN `ssh core-01 "sha256sum /opt/klai/alloy/config.alloy"`
  matched `git show main:deploy/alloy/config.alloy | sha256sum`, EN
  `ssh core-01 "docker exec klai-core-alloy-1 sha256sum
  /etc/alloy/config.alloy"` matched dezelfde sha.

**Test:** post-merge: trigger `workflow_dispatch` op deploy-compose.yml
om de eerste run te forceren. Verifieer log + sha256.

## AC-3 — Searxng config sync

- **Given** R2 + R3 uitgevoerd voor searxng,
- **When** identiek scenario als AC-2 maar voor `deploy/searxng/
  settings.yml`,
- **Then** identieke 3-way sha256 verificatie tegen `/opt/klai/searxng/
  settings.yml` en `klai-core-searxng-1:/etc/searxng/settings.yml`.

## AC-4 — Vexa profiles sync (asymmetric service name)

- **Given** R2 + R3 uitgevoerd voor `runtime-api` service (NB: NIET
  een `vexa` service),
- **When** een commit `deploy/vexa/profiles.yaml` wijzigt,
- **Then** sha256 match tegen `/opt/klai/vexa/profiles.yaml` en
  `klai-core-runtime-api-1:/app/profiles.yaml`. De service-naam in
  `docker compose ps` is `runtime-api`, NIET `vexa` — bevestigt dat
  R2's expliciete service-naam-parameter werkt.

## AC-5 — Documentatie geupdated en checklist machine-leesbaar

- **Given** R4 uitgevoerd,
- **When** een ontwikkelaar de regels-set leest via
  `cat .claude/rules/klai/infra/deploy.md`,
- **Then** de sectie "Bind-mount config sync — required pattern" is
  aanwezig met:
  - Het probleem en de canonieke fix
  - De 3-stap checklist voor nieuwe bind-mounts (paths +
    sparse-checkout + helper-call)
  - De inventaris van Class A/A-dir/B/C bind-mounts inclusief de
    asymmetrie-noot voor vexa/runtime-api

**Test:** `grep -c "sync_and_recreate" .claude/rules/klai/infra/deploy.md`
returns >= 2 (helper genoemd in fix-beschrijving + checklist).
`grep -c "runtime-api" .claude/rules/klai/infra/deploy.md` returns
>= 1 (asymmetrie-noot expliciet gedocumenteerd).

## Verification commands (post-merge)

```bash
# AC-1: workflow groen + caddy idempotent (omdat we Caddyfile niet
# wijzigen in deze PR, verwacht "config unchanged")
gh run list --workflow deploy-compose.yml -L 1 \
  --json conclusion --jq '.[0].conclusion'
# expect: "success"

gh run view <run-id> --log | grep -E "caddy config (unchanged|content changed)"
# expect: één regel matching

# AC-2/3/4: 3-way sha256 per nieuwe service
for spec in \
  "alloy:/opt/klai/alloy/config.alloy:deploy/alloy/config.alloy:/etc/alloy/config.alloy:klai-core-alloy-1" \
  "searxng:/opt/klai/searxng/settings.yml:deploy/searxng/settings.yml:/etc/searxng/settings.yml:klai-core-searxng-1" \
  "runtime-api:/opt/klai/vexa/profiles.yaml:deploy/vexa/profiles.yaml:/app/profiles.yaml:klai-core-runtime-api-1"; do
  svc=$(echo "$spec" | cut -d: -f1)
  host=$(echo "$spec" | cut -d: -f2)
  repo=$(echo "$spec" | cut -d: -f3)
  ctr_path=$(echo "$spec" | cut -d: -f4)
  ctr_name=$(echo "$spec" | cut -d: -f5)
  HEAD=$(git show main:"$repo" | sha256sum | awk '{print $1}')
  HOST=$(ssh core-01 "sha256sum $host" 2>/dev/null | awk '{print $1}')
  CTR=$(ssh core-01 "docker exec $ctr_name sha256sum $ctr_path" 2>/dev/null | awk '{print $1}')
  if [ "$HEAD" = "$HOST" ] && [ "$HOST" = "$CTR" ]; then
    echo "$svc: PASS"
  else
    echo "$svc: FAIL — HEAD=$HEAD HOST=$HOST CTR=$CTR"
  fi
done

# AC-5: doc-checklist aanwezig
grep -c "sync_and_recreate" .claude/rules/klai/infra/deploy.md
# expect: >= 2

grep -c "runtime-api" .claude/rules/klai/infra/deploy.md
# expect: >= 1
```
