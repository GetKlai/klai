#!/usr/bin/env bash
# Tests for post-deploy-error-watch.sh (SPEC-INFRA-AUTO-ROLLBACK-001 S11).
#
# A tiny python HTTP server stands in for VictoriaLogs and answers
# /select/logsql/stats_query from sequence files; a stub docker drops the
# `run --rm -i --network ... <image>` prefix and runs the real curl, so the
# curl flags, the basic-auth config on stdin and the reply parsing are all real.
#
# Run: bash deploy/scripts/tests/post-deploy-error-watch.test.sh

set -uo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
target="$script_dir/post-deploy-error-watch.sh"

work=$(mktemp -d)
trap 'kill "${server_pid:-0}" 2>/dev/null; rm -rf "$work"' EXIT
failures=0

cat > "$work/docker" <<'STUB'
#!/usr/bin/env bash
[[ "$1" == run ]] || { echo "stub: unexpected docker args: $*" >&2; exit 90; }
printf '%s\n' "$*" >> "$STATE/docker_argv"
while [[ "$1" != "$KLAI_CURL_IMAGE" ]]; do shift; done
shift
exec curl "$@"
STUB
chmod +x "$work/docker"

cat > "$work/server.py" <<'PY'
import base64, http.server, json, os, re, sys, urllib.parse

state = sys.argv[1]
expected = "Basic " + base64.b64encode(b'vluser:p4ss=w0"rd\\x').decode()

def pop(name):
    path = os.path.join(state, name)
    counter = path + ".n"
    n = int(open(counter).read()) + 1 if os.path.exists(counter) else 1
    open(counter, "w").write(str(n))
    lines = open(path).read().split()
    return lines[min(n, len(lines)) - 1]

class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        query = urllib.parse.parse_qs(body)["query"][0]
        open(os.path.join(state, "queries.log"), "a").write(query + "\n")
        if self.headers.get("Authorization") != expected:
            return self.reply(401, "unauthorized")
        deployed = open(os.path.join(state, "deployed_iso")).read().strip()
        window_end = re.search(r"_time:\[[^,]+, ([^)]+)\)", query).group(1)
        # The "before" query ends exactly at the deploy; the "after" query ends later.
        value = pop("before" if window_end == deployed else "after")
        if value == "ERR":
            return self.reply(500, "boom")
        if value == "JUNK":
            return self.reply(200, "<html>not json</html>")
        result = [] if value == "0" else [{"metric": {"__name__": "n"}, "value": [1, value]}]
        self.reply(200, json.dumps({"status": "success", "data": {"resultType": "vector", "result": result}}))
    def reply(self, code, text):
        self.send_response(code); self.end_headers(); self.wfile.write(text.encode())
    def log_message(self, *a): pass

srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
open(os.path.join(state, "port"), "w").write(str(srv.server_address[1]))
srv.serve_forever()
PY

export STATE="$work/state"; mkdir -p "$STATE"
python3 "$work/server.py" "$STATE" &
server_pid=$!
for _ in $(seq 50); do [[ -s "$STATE/port" ]] && break; sleep 0.1; done

# A password with a quote and a backslash in it, single-quoted the way .env files carry it.
printf '%s\n' 'OTHER=1' 'VICTORIALOGS_AUTH_USER=vluser' "VICTORIALOGS_AUTH_PASSWORD='p4ss=w0\"rd\\x'" > "$work/env"

# new_case <after sequence> [<before sequence>]
new_case() {
    rm -f "$STATE"/*.n "$STATE/queries.log" "$STATE/docker_argv"
    printf '%s\n' $1 > "$STATE/after"
    printf '%s\n' ${2:-0} > "$STATE/before"
    deployed=$(date -u +%s)
    date -u -d "@$deployed" +%Y-%m-%dT%H:%M:%SZ > "$STATE/deployed_iso" 2>/dev/null \
        || date -u -r "$deployed" +%Y-%m-%dT%H:%M:%SZ > "$STATE/deployed_iso"
}

# run_watch <service> [extra args]; sets rc and out.
run_watch() {
    local svc="$1"; shift
    out=$(KLAI_DOCKER_BIN="$work/docker" KLAI_CURL_IMAGE=curlimages/curl:test KLAI_ENV_FILE="${ENV_FILE:-$work/env}" \
          KLAI_VICTORIALOGS_URL="http://127.0.0.1:$(cat "$STATE/port")" KLAI_ERROR_WATCH_INTERVAL=1 \
          bash "$target" "$svc" "$deployed" "$@" 2>&1)
    rc=$?
}

check() {
    local desc="$1"; shift
    if "$@"; then echo "ok   - $desc"; else echo "FAIL - $desc"; failures=$((failures + 1)); fi
}

echo "# clean window"
new_case "3"
run_watch portal-api --window 0
check "exit 0" test "$rc" = 0
check "says clean with the count" grep -q 'portal-api clean: 3 error lines' <<< "$out"
check "queries the service's error level" grep -q 'service:portal-api AND level:error AND _time:\[' "$STATE/queries.log"
check "runs curl on the monitoring network" grep -q -- '--network monitoring curlimages/curl:test' "$STATE/docker_argv"
printf '%s' "$out" > "$work/out.txt"
check "no credentials in the output or the container argv" bash -c '! grep -q p4ss "$0" "$1"' "$STATE/docker_argv" "$work/out.txt"

echo "# no matching line at all"
new_case "0"
run_watch portal-api --window 0
check "empty result is a clean window" test "$rc" = 0

echo "# breach"
new_case "42" "1"
run_watch retrieval-api --window 0
check "exit 3" test "$rc" = 3
check "prints the count and the threshold" grep -q 'BREACH retrieval-api: 42 error lines.*threshold 10' <<< "$out"

echo "# exactly at the threshold is not a breach"
new_case "10"
run_watch retrieval-api --window 0
check "exit 0" test "$rc" = 0

echo "# trouble that predates the deploy is not blamed on it"
new_case "42" "30"
run_watch knowledge-ingest --window 0
check "exit 0" test "$rc" = 0
check "says why" grep -q 'not blamed on it' <<< "$out"

echo "# --threshold overrides the default"
new_case "42" "1"
run_watch retrieval-api --window 0 --threshold 100
check "42 under a threshold of 100 is clean" test "$rc" = 0

echo "# caddy counts 5xx access-log lines"
new_case "42" "1"
run_watch caddy --window 0
check "exit 3" test "$rc" = 3
check "queries status:5*" grep -q 'service:caddy AND status:5\*' "$STATE/queries.log"

echo "# query errors never look like a breach"
new_case "ERR"
run_watch portal-api --window 0
check "HTTP 500 exits 1" test "$rc" = 1
new_case "JUNK"
run_watch portal-api --window 0
check "a reply that is no count exits 1" test "$rc" = 1
new_case "42" "1"
ENV_FILE="$work/none" run_watch portal-api --window 0
check "missing credentials exit 1" test "$rc" = 1
printf '%s\n' 'VICTORIALOGS_AUTH_USER=vluser' 'VICTORIALOGS_AUTH_PASSWORD=wrong' > "$work/env-wrong"
new_case "42" "1"
ENV_FILE="$work/env-wrong" run_watch portal-api --window 0
check "a rejected login exits 1" test "$rc" = 1
check "no credentials leaked into the message" bash -c '! grep -q wrong <<< "$0"' "$out"
run_watch ../etc --window 0
check "a service name that is not a plain label exits 1" test "$rc" = 1

echo "# polling over a window"
new_case "0 25" "0"
run_watch portal-api --window 3
check "a breach on a later poll exits 3" test "$rc" = 3
new_case "ERR 3 3 3" "0"
run_watch portal-api --window 2
check "one dropped query in the window is tolerated" test "$rc" = 0
new_case "ERR" "0"
run_watch portal-api --window 4
check "three dropped queries in a row exit 1" test "$rc" = 1

if (( failures > 0 )); then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all checks passed"
