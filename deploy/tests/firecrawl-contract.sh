#!/bin/sh
# Does the pinned Firecrawl still answer what LibreChat asks it?
#
# Run this BEFORE bumping the pin in deploy/docker-compose.yml. It is the
# executable form of the check that was done by hand on 2026-09-10; the
# project rule is that a verified dependency contract lives in a test, not in
# a comment, precisely because a comment goes stale in silence.
#
# Needs Docker and outbound network. It touches nothing that is running:
# its own network, its own postgres/rabbitmq/redis, all removed on exit.
#
# It applies the memory limit and worker count from deploy/docker-compose.yml,
# because it once did not. On 2026-09-10 this harness passed against
# firecrawl 2.11.325 and the deploy then crash-looped on exit 137: the
# harness ran the image unconstrained, and 21 node processes do not fit in
# the 1G the compose file allowed. "Answers correctly" and "answers correctly
# under our limits" are different claims and only the second one is useful.
#
#   sh deploy/tests/firecrawl-contract.sh                 # test the pinned image
#   sh deploy/tests/firecrawl-contract.sh <image-ref>     # test a candidate
#
# The contract, read from @librechat/agents/src/tools/search/firecrawl.ts:
#   POST {FIRECRAWL_API_URL}/v2/scrape
#   {"url": ..., "formats": ["markdown"], "onlyMainContent": true, "timeout": 7500}
#   -> 200, {"success": true, "data": {"markdown": ..., "metadata": ...}}
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
COMPOSE="$ROOT/deploy/docker-compose.yml"
INIT_SQL="$ROOT/deploy/firecrawl-nuq-init.sql"

IMAGE=${1:-$(sed -n 's/^[[:space:]]*image:[[:space:]]*\(ghcr\.io\/firecrawl\/firecrawl[^[:space:]]*\).*/\1/p' "$COMPOSE" | head -1)}
[ -n "$IMAGE" ] || { echo "no firecrawl image found in $COMPOSE" >&2; exit 1; }

# Read the constraints out of the compose file rather than restating them, so
# this cannot drift from what actually gets deployed.
MEM_LIMIT=$(awk '/^  firecrawl-api:/{f=1} f&&/memory:/{print $2; exit}' "$COMPOSE")
WORKER_COUNT=$(awk '/^  firecrawl-api:/{f=1} f&&/NUQ_WORKER_COUNT:/{gsub(/"/,"",$2); print $2; exit}' "$COMPOSE")
[ -n "$MEM_LIMIT" ] || { echo "no memory limit found for firecrawl-api in $COMPOSE" >&2; exit 1; }
[ -n "$WORKER_COUNT" ] || { echo "no NUQ_WORKER_COUNT found for firecrawl-api in $COMPOSE" >&2; exit 1; }
echo "testing: $IMAGE (memory $MEM_LIMIT, NUQ_WORKER_COUNT $WORKER_COUNT)"

N=fc-contract-net
P=fc-contract
cleanup() {
    docker rm -f ${P}-api ${P}-pg ${P}-mq ${P}-redis >/dev/null 2>&1 || true
    docker network rm $N >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM
cleanup

docker network create $N >/dev/null
docker run -d --name ${P}-pg --network $N \
    -e POSTGRES_USER=firecrawl -e POSTGRES_PASSWORD=contracttest -e POSTGRES_DB=firecrawl \
    -v "$INIT_SQL":/docker-entrypoint-initdb.d/nuq.sql:ro postgres:18.4-alpine >/dev/null
docker run -d --name ${P}-mq --network $N rabbitmq:3.13.7-alpine >/dev/null
docker run -d --name ${P}-redis --network $N redis:8.10.0-alpine >/dev/null

printf 'waiting for postgres + rabbitmq'
i=0
while [ $i -lt 90 ]; do
    if docker exec ${P}-pg pg_isready -U firecrawl >/dev/null 2>&1 &&
       docker exec ${P}-mq rabbitmq-diagnostics -q check_running >/dev/null 2>&1; then
        break
    fi
    printf '.'; sleep 2; i=$((i + 1))
done
echo

# Same ten variables deploy/docker-compose.yml sets. If a bump stops reading
# one of them, the service comes up misconfigured rather than failing, so the
# request below is what actually proves it.
docker run -d --name ${P}-api --network $N --memory "$MEM_LIMIT" \
    -e NUQ_WORKER_COUNT="$WORKER_COUNT" \
    -e PORT=3002 -e HOST=0.0.0.0 \
    -e REDIS_URL=redis://${P}-redis:6379 \
    -e REDIS_RATE_LIMIT_URL=redis://${P}-redis:6379 \
    -e USE_DB_AUTHENTICATION=false \
    -e FIRECRAWL_API_KEY=contract-test-key \
    -e NUM_WORKERS_PER_QUEUE=2 \
    -e LOGGING_LEVEL=INFO \
    -e NUQ_DATABASE_URL=postgresql://firecrawl:contracttest@${P}-pg:5432/firecrawl \
    -e NUQ_RABBITMQ_URL=amqp://${P}-mq:5672 \
    "$IMAGE" >/dev/null

printf 'waiting for the api'
READY=0
i=0
while [ $i -lt 45 ]; do
    if docker logs ${P}-api 2>&1 | grep -q 'All services running'; then READY=1; break; fi
    printf '.'; sleep 2; i=$((i + 1))
done
echo
if [ "$READY" -ne 1 ]; then
    echo "CONTRACT BROKEN — $IMAGE never reported 'All services running'" >&2
    echo "under memory $MEM_LIMIT with NUQ_WORKER_COUNT=$WORKER_COUNT." >&2
    if docker logs ${P}-api 2>&1 | grep -q "exit code 137"; then
        echo "Exit 137 in the logs: the image was killed for memory, so it" >&2
        echo "needs either a higher limit or fewer workers." >&2
    fi
    echo "Not starting at all is a contract break too: the ten env vars below" >&2
    echo "are what deploy/docker-compose.yml sets, and the harness sets exactly" >&2
    echo "those. Last lines from the container:" >&2
    docker logs --tail 20 ${P}-api 2>&1 | sed 's/^/    /' >&2
    exit 1
fi

docker exec ${P}-api node -e '
const payload = { url: "https://example.com", formats: ["markdown"], onlyMainContent: true, timeout: 7500 };
(async () => {
  const r = await fetch("http://localhost:3002/v2/scrape", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer contract-test-key" },
    body: JSON.stringify(payload),
  });
  const body = await r.json().catch(() => ({}));
  const data = body.data || {};
  const problems = [];
  if (r.status !== 200) problems.push(`expected HTTP 200, got ${r.status}`);
  if (body.success !== true) problems.push(`expected success=true, got ${JSON.stringify(body.success)}`);
  if (typeof data.markdown !== "string" || !data.markdown.length) problems.push("data.markdown is missing or empty");
  if (!data.metadata) problems.push("data.metadata is missing");
  if (problems.length) {
    console.error("CONTRACT BROKEN — LibreChat POST /v2/scrape no longer answers as expected:");
    for (const p of problems) console.error("  - " + p);
    console.error("  raw: " + JSON.stringify(body).slice(0, 300));
    process.exit(1);
  }
  console.log(`OK: 200, success=true, data={markdown(${data.markdown.length} chars), metadata}`);
})();
'

echo "resident: $(docker stats --no-stream --format '{{.MemUsage}} ({{.MemPerc}})' ${P}-api)"
