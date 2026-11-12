#!/bin/sh
# Does the pinned Zitadel accept the calls the portal login and its
# second-factor enrolment make, and does the e-mail code reach the user?
#
# The portal's auth tests mock Zitadel, so they prove the request shapes and
# nothing about whether Zitadel takes them. This test is the one place the
# login and the enrolment calls meet the pinned Zitadel before they ship.
#
# It starts, all throwaway and local: the Zitadel and Postgres images pinned in
# deploy/docker-compose.yml, klai-mailer built from klai-mailer/Dockerfile as
# Zitadel's HTTP notification provider (the production mail path), Redis for
# the mailer's nonce store and Mailpit as the mailer's SMTP server. Then it runs
# klai-portal/backend/tests/contract/test_zitadel_login_contract.py, which
# drives the portal's own route handlers with the real ZitadelClient, prints
# every Zitadel call with its status and reads the codes from the mails
# klai-mailer sent. Last, every notification Zitadel posted to klai-mailer is
# listed with the mailer's answer; any answer but 200 or 204 fails the run,
# because Zitadel retries and eventually gives up on a failing notification.
#
# Zitadel runs with the production ZITADEL_HTTPCLIENT_DENYLIST on a network
# inside 172.18.0.0/16, the klai-net subnet that denylist leaves open, so the
# run also proves Zitadel may call the mailer at its production address range.
#
# Do not loosen the module to pass a Zitadel bump; fix the portal or the mailer.
#
# Needs Docker, uv and python3.
# Usage: zitadel-login-contract.sh
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
COMPOSE="$ROOT/deploy/docker-compose.yml"
MAILPIT=axllent/mailpit:v1.33.1

service_line() { awk -v svc="  $1:" -v key="$2" '$0==svc{f=1; next} f && /^  [A-Za-z0-9_-]+:/{f=0} f && $1==key{sub(/^[^:]*: */, ""); gsub(/"/, ""); print; exit}' "$COMPOSE"; }
ZITADEL=$(service_line zitadel image:)
POSTGRES=$(service_line postgres image:)
REDIS=$(service_line redis image:)
DENYLIST=$(service_line zitadel ZITADEL_HTTPCLIENT_DENYLIST:)
for v in ZITADEL POSTGRES REDIS DENYLIST; do
    eval "[ -n \"\$$v\" ]" || { echo "no $v found in $COMPOSE" >&2; exit 1; }
done
echo "testing: $ZITADEL on $POSTGRES, notifications through klai-mailer"

P=zitadel-contract-$$
NET="172.18.$(($$ % 200 + 20))"
MAILER_IP="$NET.10"
PATDIR=$(mktemp -d)
chmod 777 "$PATDIR"
cleanup() {
    docker rm -f -v ${P}-idp ${P}-db ${P}-mail ${P}-redis ${P}-mailer >/dev/null 2>&1 || true
    docker network rm ${P}-net >/dev/null 2>&1 || true
    docker rmi ${P}-mailer:local >/dev/null 2>&1 || true
    rm -rf "$PATDIR"
}
trap cleanup EXIT HUP INT TERM

# Zitadel resolves its instance from the Host header, so the host port must be
# the port it is told it is reachable on.
PORT=$(python3 -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])")

docker build -q -f "$ROOT/klai-mailer/Dockerfile" -t ${P}-mailer:local "$ROOT" >/dev/null
docker network create --subnet "$NET.0/24" ${P}-net >/dev/null
docker run -d --name ${P}-db --network ${P}-net \
    -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=contract "$POSTGRES" >/dev/null
docker run -d --name ${P}-redis --network ${P}-net "$REDIS" >/dev/null
docker run -d --name ${P}-mail --network ${P}-net -p 127.0.0.1::8025 \
    -e MP_SMTP_AUTH_ACCEPT_ANY=1 -e MP_SMTP_AUTH_ALLOW_INSECURE=1 "$MAILPIT" >/dev/null

i=0
until docker exec ${P}-db pg_isready -U postgres >/dev/null 2>&1; do
    i=$((i + 1)); [ $i -lt 60 ] || { echo "postgres did not start" >&2; docker logs ${P}-db >&2; exit 1; }
    sleep 1
done

docker run -d --name ${P}-idp --network ${P}-net -p 127.0.0.1:${PORT}:8080 -v "$PATDIR":/pat \
    -e ZITADEL_DATABASE_POSTGRES_HOST=${P}-db \
    -e ZITADEL_DATABASE_POSTGRES_PORT=5432 \
    -e ZITADEL_DATABASE_POSTGRES_DATABASE=zitadel \
    -e ZITADEL_DATABASE_POSTGRES_USER_USERNAME=zitadel \
    -e ZITADEL_DATABASE_POSTGRES_USER_PASSWORD=contract \
    -e ZITADEL_DATABASE_POSTGRES_USER_SSL_MODE=disable \
    -e ZITADEL_DATABASE_POSTGRES_ADMIN_USERNAME=postgres \
    -e ZITADEL_DATABASE_POSTGRES_ADMIN_PASSWORD=contract \
    -e ZITADEL_DATABASE_POSTGRES_ADMIN_SSL_MODE=disable \
    -e ZITADEL_TLS_ENABLED=false \
    -e ZITADEL_EXTERNALDOMAIN=localhost \
    -e ZITADEL_EXTERNALPORT="$PORT" \
    -e ZITADEL_EXTERNALSECURE=false \
    -e ZITADEL_HTTPCLIENT_DENYLIST="$DENYLIST" \
    -e ZITADEL_FIRSTINSTANCE_PATPATH=/pat/pat.txt \
    -e ZITADEL_FIRSTINSTANCE_ORG_MACHINE_MACHINE_USERNAME=contract-admin \
    -e ZITADEL_FIRSTINSTANCE_ORG_MACHINE_MACHINE_NAME=contract-admin \
    -e ZITADEL_FIRSTINSTANCE_ORG_MACHINE_PAT_EXPIRATIONDATE=2099-01-01T00:00:00Z \
    "$ZITADEL" start-from-init --masterkey MasterkeyNeedsToHave32Characters >/dev/null

# /debug/ready answers before the API gateway does, so wait for an
# authenticated call instead.
printf 'waiting for zitadel'
i=0
until [ -s "$PATDIR/pat.txt" ] && curl -sf "http://localhost:${PORT}/admin/v1/policies/login" \
    -H "Authorization: Bearer $(cat "$PATDIR/pat.txt" 2>/dev/null)" >/dev/null 2>&1; do
    i=$((i + 1)); [ $i -lt 90 ] || { echo; echo "zitadel did not become ready" >&2; docker logs ${P}-idp 2>&1 | tail -n 40 >&2; exit 1; }
    printf '.'; sleep 2
done
echo
PAT=$(cat "$PATDIR/pat.txt")

# Zitadel generates the signing secret when the HTTP provider is created, so
# the mailer starts after it, with that secret as WEBHOOK_SECRET.
PROVIDER=$(curl -sf -X POST "http://localhost:${PORT}/admin/v1/email/http" \
    -H "Authorization: Bearer $PAT" -H 'Content-Type: application/json' \
    -d "{\"endpoint\": \"http://${MAILER_IP}:8000/notify\", \"description\": \"klai-mailer\"}")
PROVIDER_ID=$(printf '%s' "$PROVIDER" | python3 -c "import json, sys; print(json.load(sys.stdin)['id'])")
SIGNING_KEY=$(printf '%s' "$PROVIDER" | python3 -c "import json, sys; print(json.load(sys.stdin)['signingKey'])")

# PORTAL_API_URL points nowhere: the mailer's language lookup then answers
# "unknown", which it tolerates, as it does when portal-api is down.
docker run -d --name ${P}-mailer --network ${P}-net --ip "$MAILER_IP" \
    -e SMTP_HOST=${P}-mail -e SMTP_PORT=1025 -e SMTP_TLS=false \
    -e SMTP_USERNAME=contract -e SMTP_PASSWORD=contract -e SMTP_FROM=noreply@example.com \
    -e WEBHOOK_SECRET="$SIGNING_KEY" -e INTERNAL_SECRET=contract-internal \
    -e PORTAL_INTERNAL_SECRET=contract-portal -e PORTAL_API_URL=http://${P}-portal-absent:8010 \
    -e REDIS_URL=redis://${P}-redis:6379/0 \
    ${P}-mailer:local >/dev/null
curl -sf -X POST "http://localhost:${PORT}/admin/v1/email/${PROVIDER_ID}/_activate" \
    -H "Authorization: Bearer $PAT" -H 'Content-Type: application/json' -d '{}' >/dev/null

i=0
until docker exec ${P}-mailer python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; do
    i=$((i + 1)); [ $i -lt 30 ] || { echo "klai-mailer did not start" >&2; docker logs ${P}-mailer >&2; exit 1; }
    sleep 1
done

MAIL_PORT=$(docker port ${P}-mail 8025 | head -n 1 | sed 's/.*://')

status=0
(
    cd "$ROOT/klai-portal/backend"
    ZITADEL_CONTRACT_BASE_URL="http://localhost:${PORT}" \
    ZITADEL_CONTRACT_PAT="$PAT" \
    ZITADEL_CONTRACT_MAIL_URL="http://127.0.0.1:${MAIL_PORT}" \
    uv run --frozen pytest tests/contract/test_zitadel_login_contract.py \
        -m zitadel_live -v -s -rs --tb=short -p no:cacheprovider
) || status=$?

# Every notification Zitadel posted and what klai-mailer answered, in order.
echo "--- notifications posted to klai-mailer (event type -> mailer answer) ---"
docker logs ${P}-mailer > "$PATDIR/mailer.log" 2>&1
python3 - "$PATDIR/mailer.log" <<'PY' || status=1
import re
import sys

types, answers = [], []
for line in open(sys.argv[1]):
    if m := re.search(r"Received notification type=(\S+)", line):
        types.append(m.group(1))
    if m := re.search(r'"POST /notify HTTP/1.1" (\d+)', line):
        answers.append(m.group(1))
for i, answer in enumerate(answers):
    print(f"  {types[i] if i < len(types) else '(type not logged)'} -> {answer}")
bad = [a for a in answers if a not in ("200", "204")]
if not answers or bad:
    print(f"klai-mailer answered {bad or 'no notification at all'}", file=sys.stderr)
    sys.exit(1)
PY

if [ $status -ne 0 ]; then
    echo "--- zitadel log (tail) ---" >&2
    docker logs ${P}-idp 2>&1 | tail -n 60 >&2
    echo "--- klai-mailer log (tail) ---" >&2
    docker logs ${P}-mailer 2>&1 | tail -n 40 >&2
fi
exit $status
