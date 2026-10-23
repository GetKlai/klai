#!/usr/bin/env bash
# Runs scripts/widget-probe.py against a local stub of the two endpoints the
# widget uses (GET /partner/v1/widget-config, POST /partner/v1/chat/completions)
# and checks the verdict line for each way a customer's answer can go wrong.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PROBE="$ROOT/scripts/widget-probe.py"
tmp=$(mktemp -d)
stub_pid=""
cleanup() { [[ -z "$stub_pid" ]] || kill "$stub_pid" 2>/dev/null || true; rm -rf "$tmp"; }
trap cleanup EXIT

SECRET_ID="wgt_probe_secret_id_0123"
SESSION_TOKEN="session-token-must-not-leak"

cat > "$tmp/stub.py" <<'PY'
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

MODE, WIDGET_ID, TOKEN = os.environ["MODE"], os.environ["STUB_WIDGET_ID"], os.environ["STUB_TOKEN"]

def sse(h, *payloads):
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.end_headers()
    for p in payloads:
        h.wfile.write(b"data: " + (p if isinstance(p, bytes) else json.dumps(p).encode()) + b"\n\n")
        h.wfile.flush()

def delta(**d):
    return {"choices": [{"index": 0, "delta": d}]}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        # The mint the widget does: public widget id, an allowed Origin, a session id.
        ok = (self.path.startswith("/partner/v1/widget-config?id=" + WIDGET_ID)
              and self.headers.get("Origin") == "https://probe.example.com"
              and self.headers.get("X-Klai-Widget-Session-Id"))
        if not ok:
            self.send_response(403); self.end_headers(); return
        body = json.dumps({"chat_endpoint": "/partner/v1/chat/completions", "session_token": TOKEN}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if (self.path != "/partner/v1/chat/completions"
                or self.headers.get("Authorization") != "Bearer " + TOKEN
                or body.get("stream") is not True or not body.get("messages")):
            self.send_response(401); self.end_headers(); return
        if MODE == "http500":
            self.send_response(500); self.end_headers(); return
        if MODE == "slow":
            time.sleep(1.5)
        if MODE == "stream_error":
            return sse(self, {"error": {"message": "upstream_error"}}, b"[DONE]")
        source = delta(sources=[{"label": "1", "title": "Probe doc", "url": ""}])
        if MODE == "empty":
            return sse(self, source, delta(content=""), b"[DONE]")
        frames = [delta(content="Het supportteam is er van 08:15 tot 17:45.")]
        if MODE != "no_sources":
            frames.insert(0, source)
        sse(self, *frames, b"[DONE]")

srv = HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(srv.server_port))
srv.serve_forever()
PY

fails=0
# run <mode> <expected status> <expected reason> [extra env as VAR=value ...]
run() {
    local mode="$1" want_status="$2" want_reason="$3"; shift 3
    local port_file="$tmp/port.$mode" url out got
    rm -f "$port_file" "$tmp/log"
    MODE="$mode" STUB_WIDGET_ID="$SECRET_ID" STUB_TOKEN="$SESSION_TOKEN" python3 "$tmp/stub.py" "$port_file" &
    stub_pid=$!
    for _ in $(seq 50); do [[ -s "$port_file" ]] && break; sleep 0.1; done
    url="http://127.0.0.1:$(cat "$port_file")"
    [[ "$mode" != unreachable ]] || url="http://127.0.0.1:1"
    out=$(env WIDGET_PROBE_BASE_URL="$url" WIDGET_PROBE_WIDGET_ID="$SECRET_ID" \
        WIDGET_PROBE_ORIGIN="https://probe.example.com" WIDGET_PROBE_LOG_FILE="$tmp/log" \
        KLAI_ENV_FILE="$tmp/no-such-env" "$@" python3 -I "$PROBE" 2>&1) || true
    kill "$stub_pid" 2>/dev/null || true; wait "$stub_pid" 2>/dev/null || true; stub_pid=""
    got=$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print(d["status"], d["reason"])' <<< "$out" 2>/dev/null || echo "unparsable: $out")
    if [[ "$got" != "$want_status $want_reason" ]]; then
        echo "FAIL $mode: want '$want_status $want_reason', got '$got'" >&2; fails=$((fails + 1)); return
    fi
    # One line to stdout, the same line in the log file, never a credential.
    if [[ $(wc -l < "$tmp/log") -ne 1 || "$(cat "$tmp/log")" != "$out" ]]; then
        echo "FAIL $mode: log line differs from stdout" >&2; fails=$((fails + 1))
    fi
    if grep -q -e "$SESSION_TOKEN" -e "$SECRET_ID" <<< "$out"; then
        echo "FAIL $mode: credential printed" >&2; fails=$((fails + 1))
    fi
    echo "ok   $mode -> $want_status $want_reason"
}

run good ok ok WIDGET_PROBE_MAX_LATENCY_MS=5000
python3 - "$tmp/log" <<'PY' || { echo "FAIL good: verdict fields" >&2; fails=$((fails + 1)); }
import json, sys
d = json.loads(open(sys.argv[1]).read())
assert d["http_status"] == 200 and d["has_sources"] is True and d["answer_chars"] > 0, d
assert d["service"] == "widget-probe" and d["event"] == "widget_probe" and d["latency_ms"] >= 0 and d["ts"], d
PY
run http500 fail http
run no_sources fail no_sources
run empty fail empty_answer
run stream_error fail stream_error
run slow fail latency WIDGET_PROBE_MAX_LATENCY_MS=500
run unreachable fail unreachable

# Without the credential the probe cannot vouch for anything: that is a fail, not silence.
out=$(env -u WIDGET_PROBE_WIDGET_ID WIDGET_PROBE_LOG_FILE="$tmp/log2" KLAI_ENV_FILE="$tmp/no-such-env" python3 -I "$PROBE" || true)
grep -q '"status":"fail".*"reason":"config"' <<< "$out" || { echo "FAIL config: $out" >&2; fails=$((fails + 1)); }

[[ $fails -eq 0 ]] || { echo "$fails check(s) failed" >&2; exit 1; }
echo "widget-probe: all checks passed"
