#!/bin/sh
# SPEC-SEC-DOCKER-AUTHZ-001 REQ-U-002a — the drop filter must remove only noise.
#
# config.alloy drops docker-socket-proxy-ro's successful GETs, because routing
# Alloy through that proxy generates ~2.7M access-log lines/day that are all
# 200s on reads. The whole value of the filter depends on it NOT matching a
# denial: a 403 on the GET-only lane means something asked for a verb that lane
# does not serve, and that is the one line worth keeping.
#
# A regex is easy to widen by accident. This pins both directions against real
# log lines captured from the running proxy on 2026-08-14.

set -eu

CONFIG="${1:-deploy/alloy/config.alloy}"
FAIL=0

EXPR=$(
    uv run --quiet python -c "
import re, sys
src = open(sys.argv[1]).read()
m = re.search(r'expression\s*=\s*\"((?:[^\"\\\\]|\\\\.)*)\"', src)
if not m:
    sys.exit('no stage.drop expression found in ' + sys.argv[1])
# Undo the Alloy string escaping to get the raw RE2 pattern.
print(m.group(1).encode().decode('unicode_escape'))
" "$CONFIG"
)

echo "── alloy drop-filter guard ──"
echo "expression: $EXPR"

# Captured verbatim from klai-core-docker-socket-proxy-ro-1.
DROP_CASES="\
::ffff:172.31.0.3:42580 [14/Aug/2026:15:40:39.031] dockerfrontend dockerbackend/dockersocket 0/0/0/3/3 200 10691 - - ---- 160/160/86/86/0 0/0 \"GET /v1.53/containers/a11eecf175251fcb96864f1fa5b4e13c2881b04ebd317c36ad9164b57ebc8843/json HTTP/1.1\"
::ffff:172.31.0.3:43182 [14/Aug/2026:15:40:39.031] dockerfrontend dockerbackend/dockersocket 0/0/0/3/3 200 8017 - - ---- 160/160/85/85/0 0/0 \"GET /v1.53/containers/json?limit=0 HTTP/1.1\"
::ffff:172.31.0.3:43190 [14/Aug/2026:15:40:41.001] dockerfrontend dockerbackend/dockersocket 0/0/0/1/1 200 512 - - ---- 160/160/1/1/0 0/0 \"GET /v1.53/networks HTTP/1.1\""

# Every one of these must survive: they are the reason the lane is logged at all.
KEEP_CASES="\
::ffff:172.31.0.3:42600 [14/Aug/2026:15:41:02.100] dockerfrontend dockerbackend/dockersocket 0/0/0/0/0 403 217 - - ---- 160/160/1/1/0 0/0 \"POST /v1.53/containers/create HTTP/1.1\"
::ffff:172.31.0.3:42601 [14/Aug/2026:15:41:03.100] dockerfrontend dockerbackend/dockersocket 0/0/0/0/0 403 217 - - ---- 160/160/1/1/0 0/0 \"GET /v1.53/images/json HTTP/1.1\"
::ffff:172.31.0.3:42602 [14/Aug/2026:15:41:04.100] dockerfrontend dockerbackend/dockersocket 0/0/0/0/0 403 217 - - ---- 160/160/1/1/0 0/0 \"POST /v1.53/networks/abc/connect HTTP/1.1\"
::ffff:172.31.0.3:42603 [14/Aug/2026:15:41:05.100] dockerfrontend dockerbackend/dockersocket 0/0/0/0/0 502 0 - - ---- 160/160/1/1/0 0/0 \"GET /v1.53/containers/json HTTP/1.1\"
::ffff:172.31.0.3:42604 [14/Aug/2026:15:41:06.100] dockerfrontend dockerbackend/dockersocket 0/0/0/0/0 200 30 - - ---- 160/160/1/1/0 0/0 \"DELETE /v1.53/containers/abc HTTP/1.1\""

check() {
    want="$1"; cases="$2"
    echo "$cases" | while IFS= read -r line; do
        [ -n "$line" ] || continue
        got=$(EXPR="$EXPR" LINE="$line" uv run --quiet python -c "
import os, re
print('match' if re.search(os.environ['EXPR'], os.environ['LINE']) else 'no-match')
")
        short=$(echo "$line" | sed 's/.*"\(.*\)".*/\1/')
        if [ "$got" = "$want" ]; then
            echo "OK:   $want — $short"
        else
            echo "FAIL: expected $want, got $got — $short" >&2
            echo "FAILED" >> /tmp/.alloy_drop_guard_fail
        fi
    done
}

rm -f /tmp/.alloy_drop_guard_fail
echo "-- must be DROPPED (noise) --"
check match "$DROP_CASES"
echo "-- must be KEPT (signal) --"
check no-match "$KEEP_CASES"

if [ -f /tmp/.alloy_drop_guard_fail ]; then
    rm -f /tmp/.alloy_drop_guard_fail
    FAIL=1
fi

echo "─────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "alloy drop-filter guard: OK"
else
    echo "alloy drop-filter guard: FAILED" >&2
fi
exit "$FAIL"
