# SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 — Research

## 1. Het gat (huidige staat)

### 1.1 caddy.yml workflow (post-merge image rebuild)

`.github/workflows/caddy.yml` triggert op:
```yaml
push:
  branches: [main]
  paths:
    - 'deploy/caddy/**'              # te breed
    - '.github/workflows/caddy.yml'
```

Deploy step:
```yaml
- name: Deploy to core-01
  uses: appleboy/ssh-action@v1
  ...
  script: |
    docker pull ghcr.io/getklai/caddy-hetzner:latest
    /opt/klai/scripts/compose-up.sh caddy   # SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-3
```

Geen sync-stap voor de Caddyfile zelf. Dat is het gat.

### 1.2 Compose mount

`deploy/docker-compose.yml`:
```yaml
caddy:
  image: ghcr.io/getklai/caddy-hetzner:latest
  volumes:
    - ./caddy/Caddyfile:/etc/caddy/Caddyfile      # bind-mount HOST
    - caddy-tenants:/etc/caddy/tenants            # named volume
    - caddy-data:/data
    - caddy-config:/config
    - /opt/klai/caddy-logs:/var/log/caddy
```

`./caddy/Caddyfile` resolved via compose's CWD `/opt/klai/`, dus
host-pad is `/opt/klai/caddy/Caddyfile`. Dit pad wordt door geen
enkele workflow gepopuleerd of geüpdatet — alleen handmatig via scp.

### 1.3 Dockerfile

```dockerfile
FROM caddy:2.11.2-alpine
RUN apk update && apk upgrade --no-cache nghttp2 nghttp2-libs
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
```

Geen `COPY Caddyfile`. De Caddyfile is puur een bind-mount in runtime.
Image-baked is op dit moment fysiek niet geïmplementeerd.

### 1.4 Caddyfile globals

`deploy/caddy/Caddyfile`:
```caddyfile
{
    email {$ADMIN_EMAIL}
    admin off                  # ← Admin API uit; reload via API niet mogelijk
    ...
}
```

`admin off` is de canonieke Klai-config. Bevestigd in
`klai-portal/backend/app/services/provisioning/infrastructure.py
::_reload_caddy`:
```python
def _reload_caddy() -> None:
    """admin off disables the Admin API so caddy reload cannot work.
    Restart is the correct approach — ~1s TLS interruption,
    acceptable at current scale."""
    client = docker.from_env()
    caddy = client.containers.get(settings.caddy_container_name)
    caddy.restart(timeout=10)
```

Implicatie: het "fast config reload" voordeel van image-baked + admin-
API valt voor Klai weg. Container-recreate is sowieso de reload-route.

## 2. Bestaande precedent: Grafana provisioning sync

`.github/workflows/deploy-compose.yml` heeft het gewenste patroon al
voor Grafana (toegevoegd in SPEC-OBS-001 Phase C):

```bash
mkdir -p /opt/klai/grafana/provisioning
RSYNC_PROV_CHANGES=$(rsync -ac --itemize-changes deploy/grafana/provisioning/ /opt/klai/grafana/provisioning/ | grep -E '^[<>*]' || true)
if [ -n "$RSYNC_PROV_CHANGES" ]; then
  echo "Grafana provisioning content changed:"
  echo "$RSYNC_PROV_CHANGES"
  docker compose --project-directory /opt/klai up -d --force-recreate grafana
else
  echo "Grafana provisioning unchanged; using plain up -d ..."
  docker compose --project-directory /opt/klai up -d grafana
fi
```

Belangrijke observaties:
- `rsync -ac --itemize-changes` gebruikt content-checksums (`-c`),
  niet mtime. Een fresh git clone op core-01 heeft willekeurige mtime;
  zonder `-c` zou elke run "veranderd" rapporteren.
- `--force-recreate` is nodig voor bind-mount content changes; plain
  `up -d` is dan een no-op.
- Direct `docker compose ... --force-recreate` ipv `compose-up.sh`
  bypass'ed de SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-3 wrapper.
  Trade-off geaccepteerd in Grafana-precedent; dezelfde logic geldt
  voor Caddy.

## 3. Industry standards onderzocht

### 3.1 Image-baked Caddyfile (school 1)

Voorkomt op kubernetes + GitOps stacks (ArgoCD, Flux) waar de image-
hash de single source of truth is voor "deployed config".

Pros bij Klai:
- Volledig reproduceerbaar (image == config + binary).
- Trivy scan dekt ook config-syntax indirect (via build).

Cons bij Klai:
- Caddy `admin off` policy: geen `caddy reload` voordeel.
- Tenant Caddyfiles MOETEN extern blijven (volume, runtime door
  portal-api geschreven). Resultaat: split tussen image-baked main
  Caddyfile en volume-baked tenant Caddyfiles. Inconsistent mental
  model.
- Image rebuild voor elke comment-toevoeging is 5-10 minuten waste.
- Env vars (`{$ADMIN_EMAIL}`, `{$DOMAIN}`) MOETEN nog steeds runtime
  geleverd worden via `.env` — image-baked verlost niet van runtime
  config-injection.

Conclusie: voor Klai meer kosten dan baten.

### 3.2 External config + sync (school 2)

Standaard patroon op single-host docker-compose deploys met CI-
controlled config files. Bekend van bv. nginx/traefik deploys op
DigitalOcean/Hetzner stacks.

Pros bij Klai:
- Identiek patroon aan Grafana provisioning (precedent bestaat,
  bewezen stabiel sinds april 2026).
- Snel (rsync ~30s vs 5-10 min image rebuild).
- Tenant Caddyfile architectuur is uniform extern (volume) →
  consistente mental model.
- Image lifecycle ontkoppeld van config lifecycle (Renovate driven
  upgrades vs daily-config-tweaks).

Cons:
- Sync stap is een moving part (kan falen).
- Reload-failure mode moet expliciet afgedekt (R4 health check).
- Bind-mount-only changes triggeren geen recreate via plain `up -d`
  → R1 expliciet `--force-recreate`.

Conclusie: aansluiten bij bestaande precedent. Trade-offs zijn
hanteerbaar via R3 (validate) + R4 (health check).

### 3.3 Hybride (image-baked + Admin API reload)

Niet onderzocht voor Klai — `admin off` is bewust beleid (security:
expose admin endpoint = risk surface vergroten). Een hybride zou een
SPEC over admin-API expose vereisen, separate scope.

## 4. Tenant Caddyfile mechanisme (verkenning)

`klai-portal/backend/app/services/provisioning/infrastructure.py
::_write_tenant_caddyfile`:
```python
def _write_tenant_caddyfile(slug: str) -> None:
    domain = settings.domain
    tenants_path = Path(settings.caddy_tenants_path)
    tenants_path.mkdir(parents=True, exist_ok=True)
    content = f"""# Tenant: {slug}
chat-{slug}.{domain} {{
    ...
    reverse_proxy librechat-{slug}:3080
}}
"""
    tenant_file = tenants_path / f"{slug}.caddyfile"
    tenant_file.write_text(content)
```

- `settings.caddy_tenants_path` resolved naar `/caddy/tenants` in de
  portal-api container.
- portal-api heeft `caddy-tenants:/caddy/tenants` mount.
- caddy heeft `caddy-tenants:/etc/caddy/tenants` mount.
- Hoofd-Caddyfile importeert via `import /etc/caddy/tenants/*.caddyfile`.

Architectuur is zelf-besloten en runtime. Sync van een statische
versie via CI is hier ongewenst (per-tenant bestanden, dynamic). R5
expliciet out-of-scope.

## 5. Caddy validate semantiek

`caddy validate --adapter caddyfile --config <file>` parsed het bestand,
expandeert env-vars (`{$VAR}` → empty string of env-value), en
faalt op syntax fouten. Vereist:

- Caddy binary met dezelfde xcaddy plugins als prod (Hetzner DNS,
  ratelimit) — anders weigert validate plugin-directives. Daarom
  validate via de prod-image (`ghcr.io/getklai/caddy-hetzner:latest`).
- Niet-resolvbare `import /path/*.caddyfile` met geen-matches: Caddy
  2 silently OK. Bevestigd in Caddy v2.7+ source en docs.
  Mitigation: mount expliciet een lege tmp-dir om semantiek te
  pinnen.

## 6. Workflow-trigger specifieke overwegingen

### `caddy.yml` blijft op `push: branches: [main]`

Image build + push is post-merge. PR-side validatie van Dockerfile
gebeurt niet via deze workflow. Trivy scan (post-build) is een
afzonderlijke job; gate-functie ontbreekt nu pre-merge. Niet in scope
van deze SPEC.

### `caddy-validate.yml` op `pull_request:`

Pre-merge syntax-gate. Triggert ook op Dockerfile/build.sh changes
(plugin-removal kan bestaande Caddyfile breken). Trade-off: ~30s
extra CI per PR die de paths raakt. Acceptabel.

### `deploy-compose.yml` op `push: branches: [main]`

Sync-only, post-merge. Geen pre-merge gate; daar dient
`caddy-validate.yml` voor. Health check (R4) draait wel post-merge,
faalt CI als caddy niet recover. Operator's recovery: `git revert`.

## 7. Failure modes overwogen

| Mode | Detection | Response |
|---|---|---|
| Caddyfile syntax error | `caddy-validate.yml` (R3) pre-merge | PR check fails, blokkeert merge |
| Sync to host fails (rsync error) | exit-code in CI | Workflow fails, geen container-restart, oude config blijft draaien |
| Caddy crash na recreate | R4 health check (5x 2s loop) | Workflow fails, log-dump, operator runs `git revert` |
| Caddy alive maar verkeerde config (semantically wrong) | Externe Kuma uptime monitor | Buiten SPEC scope; bestaande alert-stack |
| Race tussen `caddy.yml` en `deploy-compose.yml` | n.v.t. | Beiden eindigen op `up -d`-style; laatste wint, max ~2s downtime |
| `:latest` validate-image vs deploy-image drift | n.v.t. | Window is 1 PR; klein risico geaccepteerd |

## 8. Sources

- `.github/workflows/caddy.yml` (huidige workflow)
- `.github/workflows/deploy-compose.yml` (Grafana precedent)
- `deploy/caddy/Dockerfile` (image-baked status)
- `deploy/caddy/Caddyfile` (`admin off` directive)
- `deploy/docker-compose.yml` (mount config)
- `klai-portal/backend/app/services/provisioning/infrastructure.py`
  `_write_tenant_caddyfile`, `_reload_caddy` (tenant mechanisme)
- `.claude/rules/klai/pitfalls/process-rules.md`
  `docker-compose-restart-vs-recreate (CRIT)` — `up -d --force-recreate`
  vs `restart` semantiek
- SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-3 — `compose-up.sh` als
  canonieke wrapper
- SPEC-OBS-001 Phase C — content-aware Grafana provisioning sync
  patroon
- Caddy v2 docs — `import` met glob, `caddy validate` semantiek,
  `admin off`
