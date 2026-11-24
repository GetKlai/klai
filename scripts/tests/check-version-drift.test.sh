#!/usr/bin/env bash
# check-version-drift.sh must fail on a Renovate automerge PR that is days old
# with a failed check or pending past any release age, and name it, while a PR
# that is only waiting out its release age is noted, not failed. gh and curl are stubbed on
# PATH; the stub applies the script's own --jq filter to canned JSON, so the
# real age and author filters run. Holds come from a list of their own, never the
# repository's, so a real hold expiring cannot turn this test red.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"

cat > "$tmp/bin/gh" <<'STUB'
#!/usr/bin/env bash
# gh <group> <cmd> ... --jq <expr>: run <expr> over $STUB_DIR/<group>-<cmd>.json;
# gh api only answers issue events, from $STUB_DIR/issue-events.json.
if [ "$1" = api ]; then
  case "$*" in */events*) file="$STUB_DIR/issue-events.json" ;; *) exit 1 ;; esac
else
  file="$STUB_DIR/$1-$2.json"
fi
while [ $# -gt 0 ]; do [ "$1" = --jq ] && { expr=$2; break; }; shift; done
jq -r "$expr" "$file"
# Like the real gh, `pr checks` exits 8 while a check is still pending.
[ "$1 $2" = "pr checks" ] && jq -e 'any(.bucket == "pending")' "$file" >/dev/null && exit 8
exit 0
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
checks() { jq -n "[{bucket: \"$1\", name: \"$2\"}, {bucket: \"pass\", name: \"quality\"}]" > "$tmp/pr-checks.json"; }
echo '[]' > "$tmp/issue-events.json"
# The dashboard carries only the notice timestamp-optional causes on purpose.
jq -n '[{title: "Dependency Dashboard", author: {login: "app/klai-renovate"}, body: "## Repository Problems\n\nRenovate tried to run on this repository, but found these problems.\n\n - WARN: Some release(s) did not have a releaseTimestamp, but as we are running with minimumReleaseAgeBehaviour=timestamp-optional, proceeding.\n"}]' > "$tmp/issue-list.json"
jq -n '[{createdAt: (now - 3600 | todate)}]' > "$tmp/run-list.json"

: > "$tmp/holds.txt"
export HOLDS_FILE="$tmp/holds.txt"
run_script() {
  (cd "$root" && STUB_DIR="$tmp" PATH="$tmp/bin:$PATH" REPOS=GetKlai/klai bash scripts/check-version-drift.sh 2>/dev/null) || true
}
fail() { echo "FAIL: $1" >&2; printf '%s\n' "$out" >&2; exit 1; }

# 4-day-old automerge PR with a failed check: the script exits non-zero and
# names it; the fresh, non-automerge and human PRs are not reported.
checks fail quality-gate
if (cd "$root" && STUB_DIR="$tmp" PATH="$tmp/bin:$PATH" REPOS=GetKlai/klai bash scripts/check-version-drift.sh >/dev/null 2>&1); then
  echo "FAIL: exit 0 with a 4-day-old red automerge PR" >&2; exit 1
fi
out=$(run_script)
grep -q 'FAIL: GetKlai/klai: automerge PR #101 open 4d is stuck' <<<"$out" || fail "stuck PR #101 not reported"
grep -q 'not green: fail quality-gate' <<<"$out" || fail "failing check not printed"
grep -qE '#10[234] open' <<<"$out" && fail "reported a PR that is not a stuck automerge PR"

# Same PR only pending (a newer release inside its 7-day wait): waiting, not stuck.
checks pending renovate/stability-days
out=$(run_script)
grep -q 'automerge PR #101' <<<"$out" && fail "a PR that is only waiting was reported as stuck"
grep -q 'NOTE: GetKlai/klai: #101 open 4d, waiting' <<<"$out" || fail "waiting PR not noted"

# A CI check pending for days is not a release-age wait.
checks pending quality-gate
out=$(run_script)
grep -q 'FAIL: GetKlai/klai: automerge PR #101 open 4d is stuck' <<<"$out" || fail "PR held by a pending CI check not reported"

# Pending on the release age for 11 days: no release age explains that any more.
checks pending renovate/stability-days
jq '(.[] | select(.number == 101) | .createdAt) |= (now - 11 * 86400 | todate)' "$tmp/pr-list.json" > "$tmp/pr-list.tmp" && mv "$tmp/pr-list.tmp" "$tmp/pr-list.json"
out=$(run_script)
grep -q 'FAIL: GetKlai/klai: automerge PR #101 open 11d is stuck' <<<"$out" || fail "11-day pending PR not reported"

grep -q "Dashboard has a 'Repository Problems' section" <<<"$out" && fail "the timestamp-optional notice was reported as a problem"

# E2E prod-tenant red on main: its ci-failure issue open for 30 hours fails the
# run and names the issue and its age; open for 2 hours is fine.
dashboard=$(cat "$tmp/issue-list.json")
e2e_issue() { jq --argjson h "$1" '. + [{number: 2001, title: "CI failure: E2E prod-tenant", createdAt: (now - $h * 3600 | todate), author: {login: "app/github-actions"}, body: ""}]' <<<"$dashboard" > "$tmp/issue-list.json"; }
e2e_issue 30
out=$(run_script)
grep -q 'FAIL: GetKlai/klai: #2001 CI failure: E2E prod-tenant has been open 30h (limit 24h)' <<<"$out" || fail "30-hour-old E2E issue not reported"
e2e_issue 2
out=$(run_script)
grep -q '#2001 CI failure: E2E prod-tenant has been open' <<<"$out" && fail "a 2-hour-old E2E issue was reported"
grep -q 'OK:   GetKlai/klai: #2001 CI failure: E2E prod-tenant open 2h' <<<"$out" || fail "2-hour-old E2E issue not noted"

# Created five days ago but reopened two hours ago: red for two hours, not five days.
e2e_issue 120
jq -n '[{event: "closed", created_at: (now - 50 * 3600 | todate)}, {event: "reopened", created_at: (now - 2 * 3600 | todate)}]' > "$tmp/issue-events.json"
out=$(run_script)
grep -q 'OK:   GetKlai/klai: #2001 CI failure: E2E prod-tenant open 2h' <<<"$out" || fail "a reopened issue was aged from its creation"

# Holds: the 11-day pending PR #101 is stuck until a hold names it.
day() { date -u -d "$1 days" +%F 2>/dev/null || date -u -v"$1"d +%F; }
hold() { printf 'pr-101|%s|PR 101|%s|upstream releases a fix|waiting on upstream\n' "$1" "$2" > "$tmp/holds.txt"; }
out=$(run_script)
grep -q 'FAIL: GetKlai/klai: automerge PR #101' <<<"$out" || fail "an unheld stuck PR must fail"
hold GetKlai/klai "$(day +10)"
out=$(run_script)
grep -q 'FAIL: GetKlai/klai: automerge PR #101' <<<"$out" && fail "a PR inside its hold was reported as stuck"
grep -q "NOTE: GetKlai/klai: #101 open 11d is held by 'pr-101'" <<<"$out" || fail "held PR not noted"
grep -q 'NOTE: hold pr-101 (GetKlai/klai) until' <<<"$out" || fail "hold not listed"
grep -qE 'FAIL: (hold|GetKlai)' <<<"$out" && fail "a hold inside its date left a FAIL for the hold or the repository"
hold GetKlai/klai "$(day -1)"
out=$(run_script)
grep -q 'FAIL: hold pr-101 (GetKlai/klai) expired on' <<<"$out" || fail "an expired hold must fail"
grep -q 'FAIL: GetKlai/klai: automerge PR #101' <<<"$out" || fail "a PR past its hold must be stuck again"
hold GetKlai/klai-website "$(day +10)"
out=$(run_script)
grep -q 'FAIL: GetKlai/klai: automerge PR #101' <<<"$out" || fail "a hold for another repository must not hide the PR"
printf 'pr-101|GetKlai/klai|PR 101|soon|x|y\n' > "$tmp/holds.txt"
out=$(run_script)
grep -q "FAIL: hold 'pr-101'" <<<"$out" || fail "a hold without a valid date must fail"

echo "check-version-drift: PASS"
