#!/usr/bin/env bash
# check-version-drift.sh must fail on a Renovate automerge PR that has been open
# longer than AUTOMERGE_MAX_AGE_DAYS, and name it. gh and curl are stubbed on
# PATH; the stub applies the script's own --jq filter to canned JSON, so the
# real age and author filters run.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"

cat > "$tmp/bin/gh" <<'STUB'
#!/usr/bin/env bash
# gh <group> <cmd> ... --jq <expr>: run <expr> over $STUB_DIR/<group>-<cmd>.json
[ "$1" = api ] && exit 1
file="$STUB_DIR/$1-$2.json"
while [ $# -gt 0 ]; do [ "$1" = --jq ] && { expr=$2; break; }; shift; done
jq -r "$expr" "$file"
STUB
printf '#!/usr/bin/env bash\nexit 22\n' > "$tmp/bin/curl"
chmod +x "$tmp/bin/gh" "$tmp/bin/curl"

# Four PRs, ages in days: a stuck automerge PR, a fresh one, one without
# automerge, and one by a person.
jq -n '
  def pr($n; $login; $days; $body):
    {number: $n, title: "PR \($n)", author: {login: $login},
     createdAt: (now - $days * 86400 | todate), body: $body};
  [pr(101; "app/klai-renovate"; 4; "x\n🚦 **Automerge**: Enabled.\nx"),
   pr(102; "app/klai-renovate"; 1; "🚦 **Automerge**: Enabled."),
   pr(103; "app/klai-renovate"; 5; "🚦 **Automerge**: Disabled by config."),
   pr(104; "mvletter"; 4; "🚦 **Automerge**: Enabled.")]' > "$tmp/pr-list.json"
jq -n '[{bucket: "pending", name: "renovate/stability-days"}, {bucket: "pass", name: "quality"}]' > "$tmp/pr-checks.json"
echo '[]' > "$tmp/issue-list.json"
jq -n '[{createdAt: (now - 3600 | todate)}]' > "$tmp/run-list.json"

run_script() {
  (cd "$root" && STUB_DIR="$tmp" PATH="$tmp/bin:$PATH" REPOS=GetKlai/klai bash scripts/check-version-drift.sh 2>/dev/null) || true
}
fail() { echo "FAIL: $1" >&2; printf '%s\n' "$out" >&2; exit 1; }

# 4-day-old automerge PR: the script exits non-zero and prints it with its
# failing check; the fresh, non-automerge and human PRs are not reported.
if (cd "$root" && STUB_DIR="$tmp" PATH="$tmp/bin:$PATH" REPOS=GetKlai/klai bash scripts/check-version-drift.sh >/dev/null 2>&1); then
  echo "FAIL: exit 0 with a 4-day-old automerge PR" >&2; exit 1
fi
out=$(run_script)
grep -q 'FAIL: GetKlai/klai: automerge PRs open longer than 3 days' <<<"$out" || fail "no automerge failure line"
grep -q '#101 open 4d' <<<"$out" || fail "stuck PR #101 not printed"
grep -q 'not green: pending renovate/stability-days' <<<"$out" || fail "non-green check not printed"
grep -qE '#10[234] open' <<<"$out" && fail "reported a PR that is not a stuck automerge PR"

# Only the fresh automerge PR: no automerge failure.
jq '[.[] | select(.number == 102)]' "$tmp/pr-list.json" > "$tmp/pr-list.tmp" && mv "$tmp/pr-list.tmp" "$tmp/pr-list.json"
out=$(run_script)
grep -q 'OK:   GetKlai/klai: no automerge PR older than 3 days' <<<"$out" || fail "fresh automerge PR was flagged"

echo "check-version-drift: PASS"
