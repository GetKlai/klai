#!/usr/bin/env bash
# SPEC-INFRA-CONTAINER-HYGIENE-001 — the audit must know all three managed classes.
#
# vexa12-runtime creates one container per meeting. They carry upstream's labels
# (runtime.managed / runtime.workload_id), not ours, so to an audit that only
# knows klasse-A and klasse-B they look exactly like the container that got
# deleted in the librechat-voys incident: legitimate, production-relevant, and
# unlabelled by our conventions.
#
# The exemption is deliberately narrow. `runtime.managed` is upstream's generic
# name, much easier to collide with than our own klai.* labels, so the class only
# counts when the image is also from the vexaai namespace. This pins both halves:
# a real bot must be recognised, and an impostor claiming the label from another
# image must still be reported.
#
# Driven by a docker stub on PATH — no daemon required, so it runs in CI.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
AUDIT="$REPO_ROOT/deploy/scripts/docker-orphan-audit.sh"
STUB_DIR="$(mktemp -d)"
trap 'rm -rf "$STUB_DIR"' EXIT

# Fixture: name|compose-project|klai.managed_by|klai.adhoc|runtime.managed|image
cat > "$STUB_DIR/containers.txt" <<'EOF'
klai-core-portal-api-1|klai-core|||false|ghcr.io/getklai/portal-api:latest
librechat-voys||portal-api-provisioning||false|1b8fa0a19a2c
vexa-mtg-5-9a935aa1||||true|vexaai/vexa-bot:v0.12.22
debug-shell||||false|alpine:3.22
impostor-bot||||true|alpine:3.22
EOF

cat > "$STUB_DIR/docker" <<'STUB'
#!/usr/bin/env bash
# Minimal docker stub: answers `ps` and `inspect` from the fixture, and returns
# empty for everything else so the audit's other detections find nothing.
FIX="$(dirname "$0")/containers.txt"

field() { awk -F'|' -v n="$1" -v f="$2" '$1==n{print $f}' "$FIX"; }

case "$1" in
  ps)
    # Only the plain running-name listing is used by detection 1.
    for a in "$@"; do [[ "$a" == "-a" ]] && exit 0; done
    case "$*" in
      *'{{.Names}}'*) cut -d'|' -f1 "$FIX" ;;
      *) : ;;
    esac
    ;;
  inspect)
    name="$2"; tpl="$4"
    case "$tpl" in
      *com.docker.compose.project*) field "$name" 2 ;;
      *com.docker.compose.service*) : ;;
      *klai.managed_by*)            field "$name" 3 ;;
      *klai.tenant_slug*)           : ;;
      *klai.adhoc*)                 field "$name" 4 ;;
      *runtime.managed*)            field "$name" 5 ;;
      *.Config.Image*)              field "$name" 6 ;;
      *) : ;;
    esac
    ;;
  *) : ;;
esac
exit 0
STUB
chmod +x "$STUB_DIR/docker"

OUT=$(PATH="$STUB_DIR:$PATH" bash "$AUDIT" 2>/dev/null || true)

FAIL=0
flagged() { echo "$OUT" | grep -q "\"event\":\"orphan_no_managed_label\",\"container_name\":\"$1\""; }

expect() {
    want="$1"; name="$2"; why="$3"
    if [ "$want" = "flagged" ]; then
        if flagged "$name"; then echo "OK:   flagged   $name — $why"
        else echo "FAIL: $name was NOT flagged — $why" >&2; FAIL=1; fi
    else
        if flagged "$name"; then echo "FAIL: $name WAS flagged — $why" >&2; FAIL=1
        else echo "OK:   exempt    $name — $why"; fi
    fi
}

echo "── orphan-audit managed-class guard ──"
expect exempt  klai-core-portal-api-1 "klasse A — compose-project=klai-core"
expect exempt  librechat-voys         "klasse B — klai.managed_by=portal-api-provisioning"
expect exempt  vexa-mtg-5-9a935aa1    "klasse C — runtime.managed=true on a vexaai/* image"
expect flagged debug-shell            "no class and no klai.adhoc opt-in"
expect flagged impostor-bot           "claims runtime.managed from a non-vexaai image"

echo "──────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "orphan-audit managed-class guard: OK"
else
    echo "orphan-audit managed-class guard: FAILED" >&2
fi
exit "$FAIL"
