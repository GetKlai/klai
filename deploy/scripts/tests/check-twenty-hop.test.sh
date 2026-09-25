#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
compose() { printf '  crm:\n    image: twentycrm/twenty:%s\n  crm-worker:\n    image: twentycrm/twenty:%s\n' "$1" "$1" > "$tmp/$2"; }
expect() { # expect <ok|fail> <old> <new>
  compose "$2" old.yml; compose "$3" new.yml
  if bash "$here/check-twenty-hop.sh" "$tmp/old.yml" "$tmp/new.yml" >/dev/null 2>&1; then got=ok; else got=fail; fi
  [ "$got" = "$1" ] || { echo "FAIL: $2 -> $3 gave $got, expected $1" >&2; exit 1; }
}
expect ok   v2.42.4 v2.42.4
expect ok   v2.42.4 v2.42.6
expect ok   v2.42.6 v2.43.1
expect fail v2.42.6 v2.44.0
expect fail v2.43.0 v2.42.6
expect ok   v2.42.6 v3.0.2
expect fail v2.42.6 v3.1.0
echo "check-twenty-hop: PASS"
