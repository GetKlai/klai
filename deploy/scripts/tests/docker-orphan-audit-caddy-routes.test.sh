#!/usr/bin/env bash
# SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-5 — route detection must read the whole config.
#
# /opt/klai/Caddyfile ends in `import /etc/caddy/tenants/*.caddyfile`. Every
# tenant route lives behind that import, in a Docker volume, so the path is
# container-internal and absent from the host. The audit read only the main file,
# which meant no tenant route was ever visible and detection 6 reported all 42
# tenant containers as routeless — every week, while those tenants served 200.
#
# A detection that always fires cannot distinguish the case it exists to catch.
# That is the failure this pins: not "does it warn", but "does it warn only when
# the route is genuinely missing", plus the third state — if the tenant config
# cannot be read at all, say so once instead of blaming every tenant.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
AUDIT="$REPO_ROOT/deploy/scripts/docker-orphan-audit.sh"
FAIL=0

scenario() {
    # $1 = tenant dir readable? (yes|no)
    # $2 = include a route for librechat-beta?
    # $3 = ask the container for the main config instead of passing CADDYFILE? (yes|no)
    local readable="$1" beta_route="$2" resolve="${3:-no}"
    local tmp; tmp="$(mktemp -d)"

    mkdir -p "$tmp/tenants"
    cat > "$tmp/Caddyfile" <<'EOF'
example.getklai.com {
    reverse_proxy portal-api:8000
}
dev.getklai.com {
    reverse_proxy portal-api-dev:8010
}
import /etc/caddy/tenants/*.caddyfile
EOF
    cat > "$tmp/tenants/alpha.caddyfile" <<'EOF'
chat-alpha.getklai.com {
    reverse_proxy librechat-alpha:3080
}
EOF
    if [ "$beta_route" = "yes" ]; then
        cat > "$tmp/tenants/beta.caddyfile" <<'EOF'
chat-beta.getklai.com {
    reverse_proxy librechat-beta:3080
}
EOF
    fi

    # Two provisioned tenants; only alpha is guaranteed a route.
    # Field 7 is the compose-service label. At least one container must carry
    # one: the audit builds its service set with `... | grep -v '^$' | sort -u`,
    # and grep exits 1 on empty input, which under `set -euo pipefail` kills the
    # script mid-run. A fixture of only provisioning-managed containers made it
    # abort before detection 5 — silently, since set -e prints nothing — so that
    # detection was never exercised at all.
    cat > "$tmp/containers.txt" <<'EOF'
klai-core-portal-api-1|klai-core|||false|ghcr.io/getklai/portal-api:latest|portal-api
librechat-alpha||portal-api-provisioning||false|1b8fa0a19a2c|
librechat-beta||portal-api-provisioning||false|1b8fa0a19a2c|
EOF

    local tenants_src="$tmp/tenants"
    [ "$readable" = "no" ] && tenants_src="$tmp/does-not-exist"

    # A decoy beside the live file: same name, older, missing the tenant import
    # and routing librechat-alpha nowhere. Reading it looks like success and
    # produces confidently wrong findings — which is exactly what happened on
    # core-01, where /opt/klai/Caddyfile sat four months stale next to the real
    # /opt/klai/caddy/Caddyfile.
    mkdir -p "$tmp/stale"
    cat > "$tmp/stale/Caddyfile" <<'EOF'
old.getklai.com {
    reverse_proxy portal-api:8000
}
EOF
    local live_caddyfile="$tmp/Caddyfile"

    cat > "$tmp/docker" <<STUB
#!/usr/bin/env bash
FIX="$tmp/containers.txt"
field() { awk -F'|' -v n="\$1" -v f="\$2" '\$1==n{print \$f}' "\$FIX"; }
case "\$1" in
  ps)
    for a in "\$@"; do [[ "\$a" == "-a" ]] && exit 0; done
    case "\$*" in
      *'{{.Names}}'*) cut -d'|' -f1 "\$FIX" ;;
      *com.docker.compose.service*) cut -d'|' -f7 "\$FIX" ;;
      *) : ;;
    esac
    ;;
  inspect)
    if [[ "\$2" == "klai-core-caddy-1" ]]; then
      case "\$*" in
        */etc/caddy/tenants*)  echo "$tenants_src" ;;
        */etc/caddy/Caddyfile*) echo "$live_caddyfile" ;;
      esac
      exit 0
    fi
    name="\$2"; tpl="\$4"
    case "\$tpl" in
      *com.docker.compose.project*) field "\$name" 2 ;;
      *klai.managed_by*)            field "\$name" 3 ;;
      *klai.adhoc*)                 field "\$name" 4 ;;
      *runtime.managed*)            field "\$name" 5 ;;
      *.Config.Image*)              field "\$name" 6 ;;
      *) : ;;
    esac
    ;;
  *) : ;;
esac
exit 0
STUB
    chmod +x "$tmp/docker"

    if [ "$resolve" = "yes" ]; then
        # CADDYFILE unset: the audit must ask the container, not fall back to a
        # path someone guessed.
        PATH="$tmp:$PATH" \
            COMPOSE_FILE="$tmp/none.yml" OVERRIDE_FILE="$tmp/none.yml" DEV_FILE="$tmp/none.yml" \
            bash "$AUDIT" 2>/dev/null
    else
        PATH="$tmp:$PATH" CADDYFILE="$tmp/Caddyfile" \
            COMPOSE_FILE="$tmp/none.yml" OVERRIDE_FILE="$tmp/none.yml" DEV_FILE="$tmp/none.yml" \
            bash "$AUDIT" 2>/dev/null
    fi
    rm -rf "$tmp"
}

has() { echo "$1" | grep -q "\"event\":\"$2\",\"container_name\":\"$3\""; }

expect() {
    want="$1"; out="$2"; ev="$3"; name="$4"; why="$5"
    if has "$out" "$ev" "$name"; then got=present; else got=absent; fi
    if [ "$got" = "$want" ]; then
        echo "OK:   $ev $want for $name — $why"
    else
        echo "FAIL: $ev expected $want, was $got for $name — $why" >&2
        FAIL=1
    fi
}

completed() {
    # Same reason as in the klasse-C suite: set -e aborts without a word, and
    # every assertion below is about a detection that could simply never have run.
    if echo "$1" | grep -q '"event":"audit_run_completed"'; then
        echo "OK:   audit ran to completion"
    else
        echo "FAIL: audit aborted before its final event — later detections never ran" >&2
        FAIL=1
    fi
}

echo "── caddy route detection ──"

echo "-- tenant config readable, both tenants routed --"
OUT=$(scenario yes yes)
completed "$OUT"
expect absent "$OUT" tenant_container_no_route librechat-alpha "route lives behind the import"
expect absent "$OUT" tenant_container_no_route librechat-beta  "route lives behind the import"
expect absent "$OUT" caddy_config_unreadable   klai-core-caddy-1 "config was fully readable"

echo "-- tenant config readable, beta genuinely has no route --"
OUT=$(scenario yes no)
expect absent  "$OUT" tenant_container_no_route librechat-alpha "still routed"
expect present "$OUT" tenant_container_no_route librechat-beta  "this is the real signal"

echo "-- tenant config unreadable --"
OUT=$(scenario no yes)
expect present "$OUT" caddy_config_unreadable   klai-core-caddy-1 "say it once"
expect absent  "$OUT" tenant_container_no_route librechat-alpha "do not blame tenants for a config we cannot read"
expect absent  "$OUT" tenant_container_no_route librechat-beta  "do not blame tenants for a config we cannot read"

echo "-- main config resolved from the container, decoy present --"
OUT=$(scenario yes yes yes)
completed "$OUT"
# portal-api-dev appears only in the live file and has no container. Its absence
# from the findings would mean the audit read the decoy, or read nothing at all
# and skipped the detection — the two failure modes that look identical from a
# negative assertion.
expect present "$OUT" caddy_upstream_missing    portal-api-dev  "only the mounted file names this upstream"
expect absent  "$OUT" tenant_container_no_route librechat-alpha "tenant routes still resolved"
expect absent  "$OUT" tenant_container_no_route librechat-beta  "tenant routes still resolved"

echo "───────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "caddy route detection guard: OK"
else
    echo "caddy route detection guard: FAILED" >&2
fi
exit "$FAIL"
