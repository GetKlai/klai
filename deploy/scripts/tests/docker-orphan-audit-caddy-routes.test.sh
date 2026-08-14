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
    # $1 = tenant dir readable? (yes|no)   $2 = include a route for librechat-beta?
    local readable="$1" beta_route="$2"
    local tmp; tmp="$(mktemp -d)"

    mkdir -p "$tmp/tenants"
    cat > "$tmp/Caddyfile" <<'EOF'
example.getklai.com {
    reverse_proxy portal-api:8000
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
    cat > "$tmp/containers.txt" <<'EOF'
librechat-alpha||portal-api-provisioning||false|1b8fa0a19a2c
librechat-beta||portal-api-provisioning||false|1b8fa0a19a2c
EOF

    local tenants_src="$tmp/tenants"
    [ "$readable" = "no" ] && tenants_src="$tmp/does-not-exist"

    cat > "$tmp/docker" <<STUB
#!/usr/bin/env bash
FIX="$tmp/containers.txt"
field() { awk -F'|' -v n="\$1" -v f="\$2" '\$1==n{print \$f}' "\$FIX"; }
case "\$1" in
  ps)
    for a in "\$@"; do [[ "\$a" == "-a" ]] && exit 0; done
    case "\$*" in
      *'{{.Names}}'*) cut -d'|' -f1 "\$FIX" ;;
      *) : ;;
    esac
    ;;
  inspect)
    if [[ "\$2" == "klai-core-caddy-1" ]]; then echo "$tenants_src"; exit 0; fi
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

    PATH="$tmp:$PATH" CADDYFILE="$tmp/Caddyfile" \
        COMPOSE_FILE="$tmp/none.yml" OVERRIDE_FILE="$tmp/none.yml" DEV_FILE="$tmp/none.yml" \
        bash "$AUDIT" 2>/dev/null
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

echo "── caddy route detection ──"

echo "-- tenant config readable, both tenants routed --"
OUT=$(scenario yes yes)
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

echo "───────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "caddy route detection guard: OK"
else
    echo "caddy route detection guard: FAILED" >&2
fi
exit "$FAIL"
