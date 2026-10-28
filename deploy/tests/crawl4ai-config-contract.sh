#!/bin/sh
# Does the pinned crawl4ai accept the crawl config knowledge-ingest really sends?
#
# From 2026-10-23 02:01 every nightly crawl of every knowledge base fetched 0
# pages for six nights: crawl4ai 0.9.5 answers HTTP 400 "Rejected config: a
# JavaScript wait_for is not permitted from an untrusted request" to the
# `wait_for` that knowledge_ingest/crawl4ai_config.py builds. Every unit test
# stayed green, because they assert the SHAPE of the dict we build and none of
# them ever asks crawl4ai. The cookie contract next to this file proves the
# cookie, user agent and body-visibility fields; it never sent the real config.
#
# This test builds the config with the production builder and POSTs it to the
# pinned image for a page it can fetch. It fails on anything but HTTP 200 and on
# an empty `results[0].markdown.raw_markdown`: a 200 with no markdown is the
# other way a refused or ignored field shows up.
#
# EXPECTED RESULT TODAY: FAIL against 0.9.5, with the 400 above. That is the
# bug, not a flaw in the test; the config fix lands separately and turns this
# green. Do not change the config to satisfy this file, and do not loosen this
# file to pass the current config.
#
# Needs Docker and uv. No outbound network: the page is served by a second
# container on the same private network, and everything is removed on exit.
#
# Usage: crawl4ai-config-contract.sh [IMAGE]
#   IMAGE defaults to the pin in deploy/docker-compose.yml. CI passes the image
#   it built from deploy/crawl4ai, because that tag is built on core-01 and
#   never pushed to a registry.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
COMPOSE="$ROOT/deploy/docker-compose.yml"

IMAGE=${1:-$(awk '/^  crawl4ai:/{f=1} f&&/^[[:space:]]*image:/{print $2; exit}' "$COMPOSE")}
HOOKS_ENABLED=$(awk '/^  crawl4ai:/{f=1} f&&/CRAWL4AI_HOOKS_ENABLED:/{gsub(/"/,"",$2); print $2; exit}' "$COMPOSE")
[ -n "$IMAGE" ] || { echo "no crawl4ai image found in $COMPOSE" >&2; exit 1; }
[ -n "$HOOKS_ENABLED" ] || { echo "no CRAWL4AI_HOOKS_ENABLED found in $COMPOSE" >&2; exit 1; }
echo "testing: $IMAGE"

# The real builder, in the real environment. selector=None is the default
# crawl, the one that carries the wait_for predicate and the chrome stripping.
PARAMS=$(cd "$ROOT/klai-knowledge-ingest" && uv run --frozen python -c \
    "import json; from knowledge_ingest.crawl4ai_config import build_crawl_config; print(json.dumps(build_crawl_config(selector=None)))")
[ -n "$PARAMS" ] || { echo "build_crawl_config produced no output" >&2; exit 1; }

P=c4-config-contract-$$
TOKEN=contract-test-token
cleanup() {
    docker rm -f ${P}-srv ${P}-web >/dev/null 2>&1 || true
    docker network rm ${P}-net >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

docker network create ${P}-net >/dev/null

# A static page with well over the 50 words our wait_for predicate waits for,
# inside <main> so the chrome stripping leaves it alone. Served from the same
# image, which already has python.
docker run -d --name ${P}-web --network ${P}-net --entrypoint sh "$IMAGE" -c '
    mkdir /tmp/site && cd /tmp/site
    {
        echo "<!doctype html><html><head><title>Contract page</title></head><body>"
        echo "<nav>navigation that must not matter</nav><main><h1>Contract page</h1>"
        i=0
        while [ $i -lt 12 ]; do
            echo "<p>This paragraph exists so the page holds far more than fifty words of readable text, which is what the crawler waits for before it hands back markdown for this page.</p>"
            i=$((i + 1))
        done
        echo "</main></body></html>"
    } > index.html
    exec python -m http.server 8080
' >/dev/null

docker run -d --name ${P}-srv --network ${P}-net --shm-size=1g \
    -e CRAWL4AI_API_TOKEN="$TOKEN" \
    -e CRAWL4AI_HOOKS_ENABLED="$HOOKS_ENABLED" \
    "$IMAGE" >/dev/null

printf 'waiting for crawl4ai'
i=0
while [ $i -lt 60 ]; do
    docker exec ${P}-srv python -c "import httpx;httpx.get('http://localhost:11235/',timeout=3)" >/dev/null 2>&1 && break
    printf '.'; sleep 2; i=$((i + 1))
done
echo

docker exec -i -e TOKEN="$TOKEN" -e PARAMS="$PARAMS" -e PAGE="http://${P}-web:8080/" ${P}-srv python - <<'PY'
import json
import os
import sys

import httpx

payload = {
    "urls": [os.environ["PAGE"]],
    "crawler_config": {"type": "CrawlerRunConfig", "params": json.loads(os.environ["PARAMS"])},
}
r = httpx.post(
    "http://localhost:11235/crawl",
    json=payload,
    headers={"Authorization": f"Bearer {os.environ['TOKEN']}"},
    timeout=120,
)
if r.status_code != 200:
    print(f"CONTRACT BROKEN - crawl4ai answered HTTP {r.status_code} to the config "
          "knowledge_ingest/crawl4ai_config.py builds, so every crawl fails with "
          f"0 pages. Body: {r.text[:300]}", file=sys.stderr)
    raise SystemExit(1)

results = r.json().get("results") or []
first = results[0] if results else {}
markdown = first.get("markdown") or {}
raw = markdown.get("raw_markdown") if isinstance(markdown, dict) else markdown
if not raw:
    print("CONTRACT BROKEN - HTTP 200 but results[0].markdown.raw_markdown is empty "
          f"(success={first.get('success')!r}, error={str(first.get('error_message'))[:200]!r}). "
          "crawl4ai accepted the request and produced nothing from a page that "
          "holds text.", file=sys.stderr)
    raise SystemExit(1)
print(f"OK: crawl4ai accepted our crawl config and returned {len(raw)} characters of markdown.")
PY
