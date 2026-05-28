#!/usr/bin/env bash
# kb-citation-smoke.sh - production smoke for KB answer citations.
#
# Runs a real LibreChat tenant request through the normal /api/agents/chat path,
# then verifies the saved Mongo message contains:
#   - visible **Bronnen**
#   - visible **Agent activiteit**
#   - structured content[].sources with artifact/evidence metadata
#
# Run on core-01:
#   /opt/klai/scripts/kb-citation-smoke.sh
#
# Optional env overrides:
#   LIBRECHAT_CONTAINER=librechat-jantine-doornbos-37418563
#   KB_SMOKE_QUERY='Wie is waarvoor verantwoordelijk?'
#   KB_SMOKE_EXPECT_SOURCE='Verantwoordelijkheden per bouwblok'
#   KB_SMOKE_EXPECT_REFUSAL=0
#   KB_SMOKE_TIMEOUT=180

set -euo pipefail

LIBRECHAT_CONTAINER="${LIBRECHAT_CONTAINER:-librechat-jantine-doornbos-37418563}"
KB_SMOKE_QUERY="${KB_SMOKE_QUERY:-Wie is waarvoor verantwoordelijk?}"
KB_SMOKE_EXPECT_SOURCE="${KB_SMOKE_EXPECT_SOURCE:-Verantwoordelijkheden per bouwblok}"
KB_SMOKE_EXPECT_REFUSAL="${KB_SMOKE_EXPECT_REFUSAL:-0}"
KB_SMOKE_TIMEOUT="${KB_SMOKE_TIMEOUT:-180}"
MONGO_CONTAINER="${MONGO_CONTAINER:-klai-core-mongodb-1}"

log() { printf '%-14s %s\n' "$1" "$2"; }
fail() {
  log "[FAIL]" "$1" >&2
  exit 1
}

docker inspect "$LIBRECHAT_CONTAINER" >/dev/null 2>&1 || fail "$LIBRECHAT_CONTAINER not found"
docker inspect "$MONGO_CONTAINER" >/dev/null 2>&1 || fail "$MONGO_CONTAINER not found"

MONGO_URI=$(docker inspect "$LIBRECHAT_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | awk -F= '$1 == "MONGO_URI" {print substr($0, index($0, "=") + 1); exit}')

[ -n "$MONGO_URI" ] || fail "MONGO_URI not found on $LIBRECHAT_CONTAINER"
MONGO_URI_IN_MONGO_CONTAINER=$(printf '%s' "$MONGO_URI" | sed 's/@mongodb:27017/@localhost:27017/')

log "[*] tenant" "$LIBRECHAT_CONTAINER"
log "[*] query" "$KB_SMOKE_QUERY"

CONVERSATION_ID=$(
  docker exec -i "$LIBRECHAT_CONTAINER" node - "$KB_SMOKE_QUERY" <<'NODE'
const jwt = require('jsonwebtoken');
const crypto = require('crypto');

const query = process.argv[2];
const userId = process.env.KB_SMOKE_USER_ID || '6a11717f85f622ea832fa63d';
const email = process.env.KB_SMOKE_USER_EMAIL || 'info@jantinedoornbos.nl';
const token = jwt.sign(
  { id: userId, username: email, provider: 'openid', email },
  process.env.JWT_SECRET,
  { expiresIn: '15m' },
);

const payload = {
  text: query,
  sender: 'User',
  isCreatedByUser: true,
  messageId: crypto.randomUUID(),
  parentMessageId: '00000000-0000-0000-0000-000000000000',
  conversationId: 'new',
  endpoint: 'Klai AI',
  endpointType: 'custom',
  model: 'klai-primary',
  spec: 'klai-primary',
  agent_id: 'Klai AI__klai-primary___Klai AI',
  promptPrefix: 'You are a helpful AI assistant.\n',
  maxContextTokens: 17100,
  resendFiles: true,
  isTemporary: false,
};

(async () => {
  const res = await fetch('http://127.0.0.1:3080/api/agents/chat/Klai%20AI', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      authorization: `Bearer ${token}`,
      'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36',
      accept: 'text/event-stream',
    },
    body: JSON.stringify(payload),
  });
  const body = await res.text();
  if (!res.ok) {
    throw new Error(`LibreChat returned HTTP ${res.status}: ${body}`);
  }
  const parsed = JSON.parse(body);
  if (!parsed.conversationId) {
    throw new Error(`Missing conversationId in response: ${body}`);
  }
  process.stdout.write(parsed.conversationId);
})().catch((err) => {
  console.error(err.stack || err.message || String(err));
  process.exit(1);
});
NODE
)

[ -n "$CONVERSATION_ID" ] || fail "LibreChat request did not return a conversation id"
log "[*] convo" "$CONVERSATION_ID"

deadline=$(( $(date +%s) + KB_SMOKE_TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  RESULT=$(
    docker exec "$MONGO_CONTAINER" mongosh "$MONGO_URI_IN_MONGO_CONTAINER" --quiet --eval "
const cid = '$CONVERSATION_ID';
const expectSource = '$KB_SMOKE_EXPECT_SOURCE';
const assistant = db.messages.find({ conversationId: cid, sender: 'Klai AI' }).sort({ createdAt: -1 }).limit(1).toArray()[0];
if (!assistant) {
  print(JSON.stringify({ ready: false, reason: 'assistant_missing' }));
  quit(0);
}
const contentText = (assistant.content || []).map((part) => part && part.text || '').join('\\n');
const sources = (assistant.content || []).flatMap((part) => Array.isArray(part && part.sources) ? part.sources : []);
print(JSON.stringify({
  ready: true,
  hasRefusal: /Dat staat niet in de kennisbank|niet betrouwbaar beantwoorden|cannot answer this reliably/.test(contentText),
  hasBronnen: /\\*\\*Bronnen\\*\\*/.test(contentText),
  hasAgentActivity: /\\*\\*Agent activiteit\\*\\*/.test(contentText),
  hasUsedSourcesLine: /Gebruikte bronnen/.test(contentText),
  markerLeaked: /klai_sources/.test(contentText),
  sourceCount: sources.length,
  sourceTitles: sources.map((source) => source.title),
  artifactIds: sources.map((source) => source.artifact_id || ''),
  evidenceCounts: sources.map((source) => Array.isArray(source.evidence_ids) ? source.evidence_ids.length : 0),
  evidenceSnippetCounts: sources.map((source) => Array.isArray(source.evidence) ? source.evidence.length : 0),
  evidenceSnippetTextCounts: sources.map((source) =>
    Array.isArray(source.evidence)
      ? source.evidence.filter((item) => item && typeof item.text === 'string' && item.text.trim()).length
      : 0
  ),
  expectedSourcePresent: sources.some((source) => String(source.title || '').includes(expectSource)),
  retrievalLine: (contentText.match(/- Kennisbank geraadpleegd:.*$/m) || [])[0] || '',
  error: assistant.error,
  unfinished: assistant.unfinished,
}));
"
  )

  READY=$(printf '%s' "$RESULT" | python3 -c 'import json, sys
try:
    print(str(bool(json.load(sys.stdin).get("ready"))).lower(), end="")
except Exception:
    print("false", end="")
')
  if [ "$READY" = "true" ]; then
    break
  fi
  sleep 2
done

RESULT_JSON="$RESULT" python3 - "$KB_SMOKE_EXPECT_SOURCE" <<'PY'
import json
import os
import sys

expected = sys.argv[1]
expect_refusal = os.environ.get("KB_SMOKE_EXPECT_REFUSAL", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
result = json.loads(os.environ["RESULT_JSON"])
failures = []
if not result.get("ready"):
    failures.append(f"assistant response not saved ({result.get('reason') or 'unknown'})")
if result.get("error"):
    failures.append("assistant message has error=true")
if result.get("unfinished"):
    failures.append("assistant message is unfinished")
if not result.get("hasAgentActivity"):
    failures.append("visible **Agent activiteit** missing")
if result.get("markerLeaked"):
    failures.append("klai_sources marker leaked into visible content")
if not result.get("retrievalLine"):
    failures.append("agent activity retrieval line missing")
if expect_refusal:
    if not result.get("hasRefusal"):
        failures.append("strict refusal text missing")
    if result.get("hasBronnen"):
        failures.append("visible **Bronnen** should be absent for strict refusal")
    if int(result.get("sourceCount") or 0) != 0:
        failures.append("structured sources should be empty for strict refusal")
    if result.get("hasUsedSourcesLine"):
        failures.append("Gebruikte bronnen line should be absent for strict refusal")
else:
    if not result.get("hasBronnen"):
        failures.append("visible **Bronnen** missing")
    if int(result.get("sourceCount") or 0) < 1:
        failures.append("structured content[].sources missing")
    if not result.get("expectedSourcePresent"):
        failures.append(f"expected source title missing: {expected}")
    if not any(result.get("artifactIds") or []):
        failures.append("structured sources missing artifact_id")
    if not any(count > 0 for count in (result.get("evidenceCounts") or [])):
        failures.append("structured sources missing evidence_ids")
    if not any(count > 0 for count in (result.get("evidenceSnippetCounts") or [])):
        failures.append("structured sources missing evidence snippets")
    if not any(count > 0 for count in (result.get("evidenceSnippetTextCounts") or [])):
        failures.append("structured source evidence snippets missing text")
if failures:
    print(json.dumps(result, indent=2, ensure_ascii=False), file=sys.stderr)
    print(f"KB citation smoke failed: {'; '.join(failures)}", file=sys.stderr)
    sys.exit(1)
print(json.dumps(result, indent=2, ensure_ascii=False))
PY

log "[OK]" "KB citation smoke passed for $CONVERSATION_ID"
