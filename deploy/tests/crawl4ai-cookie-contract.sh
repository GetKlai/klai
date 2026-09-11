#!/bin/sh
# Do the cookies we send to crawl4ai reach the site -- and only that site's
# own request?
#
# Run this before changing the crawl4ai pin, the cookie payload, or
# CRAWL4AI_HOOKS_ENABLED. It is the executable form of the check that was
# missing: the previous test asserted the SHAPE of the dict we build, which
# stayed green for four weeks while crawl4ai silently dropped it.
#
# Three contracts, because fixing the first exposed the others:
#
#   DELIVERY  -- the request field is `hooks`; we sent `hooks_config`, which
#     is not on crawl4ai's request model, and pydantic discards an unknown
#     field without a word. HTTP 200, no cookies. Hooks must also be enabled
#     server-side; the wrong field name hid that, because with the right name
#     a disabled server answers 403 instead of a cheerful 200.
#
#   ISOLATION -- hooks are attached by mutating the crawler that serves the
#     request, and crawl4ai hands out a shared one. Nothing detaches them, so
#     cookies from one request are re-injected into later requests on the same
#     crawler. deploy/crawl4ai/apply_hook_isolation_patch.py gives a hooked
#     request its own browser; this test is what keeps that true.
#
#   BODY-VISIBILITY -- we send `body_visibility_timeout` to cap a 30s wait
#     crawl4ai performs and then ignores. crawl4ai drops an unknown config
#     field WITHOUT a word (measured on 0.9.3), so a rename upstream would
#     restore the 30s-per-page tax with no signal at all. Same trap as the
#     field name above; pinned here rather than trusted.
#
# Accepting the request is not applying the cookies, and applying them to the
# right request is not the same as applying them to only that one.
#
# Needs Docker and outbound network. Touches nothing that is running: its own
# container on its own network, named per run so parallel runs do not collide,
# removed on exit. Uses httpbin.org, which echoes back the cookies it
# received; a failure there is reported as SKIPPED, separately from a real
# contract break.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
COMPOSE="$ROOT/deploy/docker-compose.yml"

# Read image and flag from compose so this cannot drift from what we deploy.
IMAGE=${1:-$(awk '/^  crawl4ai:/{f=1} f&&/^[[:space:]]*image:/{print $2; exit}' "$COMPOSE")}
HOOKS_ENABLED=$(awk '/^  crawl4ai:/{f=1} f&&/CRAWL4AI_HOOKS_ENABLED:/{gsub(/"/,"",$2); print $2; exit}' "$COMPOSE")
[ -n "$IMAGE" ] || { echo "no crawl4ai image found in $COMPOSE" >&2; exit 1; }
[ -n "$HOOKS_ENABLED" ] || { echo "no CRAWL4AI_HOOKS_ENABLED found in $COMPOSE" >&2; exit 1; }
echo "testing: $IMAGE (CRAWL4AI_HOOKS_ENABLED=$HOOKS_ENABLED)"

P=c4-cookie-contract-$$
TOKEN=contract-test-token
cleanup() {
    docker rm -f ${P}-srv >/dev/null 2>&1 || true
    docker network rm ${P}-net >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

docker network create ${P}-net >/dev/null
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

# BODY-VISIBILITY. Against the installed source, not the docs: ask the real
# config class what it made of the field we send.
docker exec -i ${P}-srv python - <<'PY'
import sys
from crawl4ai.async_configs import CrawlerRunConfig, Provenance

def load(params):
    return CrawlerRunConfig.load({"type": "CrawlerRunConfig", "params": params},
                                 provenance=Provenance.UNTRUSTED)

sent = load({"body_visibility_timeout": 2000}).body_visibility_timeout
default = load({}).body_visibility_timeout
if sent != 2000:
    print(f"CONTRACT BROKEN - we send body_visibility_timeout=2000 and crawl4ai "
          f"made {sent!r} of it. An unknown field is dropped silently here, so the "
          "likely cause is a rename or removal upstream. Every crawled page is back "
          "to paying the full body-visibility wait, and nothing else would have told "
          "you. Set the new name in knowledge_ingest/crawl4ai_config.py.",
          file=sys.stderr)
    raise SystemExit(1)
if default <= 2000:
    print(f"NOTE: upstream default is now {default}ms, at or below our 2000ms cap. "
          "Our override has stopped buying anything; consider dropping it.",
          file=sys.stderr)
print(f"OK: body_visibility_timeout is read (2000, default {default}).")
PY
docker exec -i -e TOKEN="$TOKEN" ${P}-srv python - <<'PY'
import json, os, sys
import httpx

TOKEN = os.environ["TOKEN"]
URL = "https://httpbin.org/cookies"   # echoes the cookies it received
MARKER = "klai-cookie-contract-12345"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE = {
    "urls": [URL],
    "crawler_config": {"type": "CrawlerRunConfig",
                       "params": {"cache_mode": "bypass", "page_timeout": 30000,
                                  "body_visibility_timeout": 2000}},
}
COOKIES = [{"name": "klai_contract", "value": MARKER,
            "domain": "httpbin.org", "path": "/"}]
HOOKS = {"hooks": [{"action": "add_cookies", "params": {"cookies": COOKIES}}]}


def skip(why):
    print(f"SKIPPED — {why}. Not a contract break; retry when httpbin.org is "
          "reachable.", file=sys.stderr)
    raise SystemExit(2)


def fetch(with_cookies, label):
    """Return the echoed page body, or SKIP if the echo service misbehaved.

    A page that failed to load also answers HTTP 200 with success=true at the
    top level -- the per-page verdict is inside `results`. Without checking it,
    every httpbin hiccup would be reported as a broken cookie hook.
    """
    payload = json.loads(json.dumps(BASE))
    if with_cookies:
        payload["hooks"] = HOOKS
    r = httpx.post("http://localhost:11235/crawl", json=payload,
                   headers=HEADERS, timeout=120)
    if r.status_code == 403:
        print(f"CONTRACT BROKEN — crawl4ai refused the hook ({label}): "
              f"{r.text[:200]}\nCRAWL4AI_HOOKS_ENABLED must be true in "
              "deploy/docker-compose.yml or no authenticated crawl sends "
              "cookies.", file=sys.stderr)
        raise SystemExit(1)
    if r.status_code != 200:
        skip(f"{label}: crawl4ai returned HTTP {r.status_code}: {r.text[:200]}")
    results = r.json().get("results") or []
    if not results or not results[0].get("success"):
        err = (results[0].get("error_message") if results else "no results")
        skip(f"{label}: the echo page did not load ({str(err)[:150]})")
    return json.dumps(results[0])


# DELIVERY.
if MARKER in fetch(False, "control"):
    print("BROKEN TEST — the control request already contains the marker; "
          "the echo service is not telling us anything.", file=sys.stderr)
    raise SystemExit(1)

if MARKER not in fetch(True, "with cookies"):
    print("CONTRACT BROKEN — crawl4ai accepted the request (HTTP 200) and the "
          "cookie never reached the site. That is the failure this file "
          "exists for: a 200 says the request parsed, not that the cookie was "
          "applied. Check the request field name (`hooks`, not "
          "`hooks_config` -- an unknown field is dropped silently) and that "
          "the add_cookies action still exists in crawl4ai's hook registry.",
          file=sys.stderr)
    raise SystemExit(1)

# ISOLATION. Twice: the pooled crawler is picked per request, so one clean
# answer could just mean this request landed on a different browser.
for attempt in (1, 2):
    if MARKER in fetch(False, f"after-cookies #{attempt}"):
        print("CONTRACT BROKEN — a request that sent NO cookies came back "
              "carrying the previous request's cookie. crawl4ai attaches hooks "
              "to the crawler serving the request and hands out shared "
              "crawlers, so one tenant's session rides along on another "
              "tenant's crawl of the same site. Check that "
              "deploy/crawl4ai/apply_hook_isolation_patch.py still applies to "
              "this image -- most likely the base image moved and the pin in "
              "deploy/docker-compose.yml points at an unpatched build.",
              file=sys.stderr)
        raise SystemExit(1)

# And the hooked request still works after all that.
if MARKER not in fetch(True, "with cookies, again"):
    print("CONTRACT BROKEN — cookies arrived on the first hooked request but "
          "not on a later one.", file=sys.stderr)
    raise SystemExit(1)

print("OK: the cookie arrived at the site, and only on the requests that sent it.")
PY
