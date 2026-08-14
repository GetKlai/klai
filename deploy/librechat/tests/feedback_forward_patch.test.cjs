const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// 2026-08-13 review finding 2: SPEC-KB-015 feedback forwarding was a fully
// built, tested portal-api endpoint (POST /internal/v1/kb-feedback) that no
// live LibreChat deployment path ever called -- the standalone
// deploy/librechat/entrypoint.sh that carried the patch was wired into
// neither provisioning (_start_librechat_container) nor the getklai canary
// compose service. Decision: WIRE it into both live entrypoints (the portal
// endpoint is fully implemented and tested -- see
// klai-portal/backend/tests/test_kb_feedback_endpoint.py), fail-loud on
// anchor drift like the Meili block, and delete the dead standalone files.
// This test locks in the wiring so it cannot silently regress to "built but
// unreachable" again.

const repoRoot = path.resolve(__dirname, '../../..');
const entrypoint = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/klai-entrypoint.sh'),
  'utf8',
);
const getklaiEntrypoint = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/getklai/entrypoint.sh'),
  'utf8',
);
const dockerCompose = fs.readFileSync(path.join(repoRoot, 'deploy/docker-compose.yml'), 'utf8');
const generator = fs.readFileSync(
  path.join(repoRoot, 'klai-portal/backend/app/services/provisioning/generators.py'),
  'utf8',
);
const internalApi = fs.readFileSync(
  path.join(repoRoot, 'klai-portal/backend/app/api/internal.py'),
  'utf8',
);

// --- The dead standalone artifacts must be gone. ---
for (const deadFile of [
  'deploy/librechat/entrypoint.sh',
  'deploy/librechat/patches/feedback.cjs',
  'deploy/librechat/patches/feedback.patch',
]) {
  assert.equal(
    fs.existsSync(path.join(repoRoot, deadFile)),
    false,
    `${deadFile} must be deleted -- superseded by the wired transform in klai-entrypoint.sh / getklai/entrypoint.sh`,
  );
}

// --- Both live entrypoints embed the fail-loud feedback transform. ---
for (const [name, target] of [
  ['deploy/librechat/klai-entrypoint.sh', entrypoint],
  ['deploy/librechat/getklai/entrypoint.sh', getklaiEntrypoint],
]) {
  assert.match(target, /SPEC-KB-015/, name);
  assert.match(target, /FEEDBACK_TARGET=\/app\/api\/server\/routes\/messages\.js/, name);
  assert.match(target, /grep -q "SPEC-KB-015" "\$FEEDBACK_TARGET"/, name);
  assert.match(target, /already applied, skipping/, name);
  assert.match(
    target,
    /required SPEC-KB-015 feedback-forward patch target is missing/,
    name,
  );
  assert.match(
    target,
    /could not apply SPEC-KB-015 feedback-forward patch in \$\{target\}: expected updateFeedback\/sendFeedbackScore anchor not found/,
    name,
  );
  assert.match(target, /process\.env\.PORTAL_INTERNAL_URL/, name);
  assert.match(target, /process\.env\.PORTAL_INTERNAL_SECRET/, name);
  assert.match(target, /internal\/v1\/kb-feedback/, name);
  // The tenant id must come from KLAI_ORG_SLUG (always set on every tenant
  // env -- see generators.py), not req.user?.tenantId, which LibreChat's
  // OIDC user object never populates. Sending tenantId: null on every call
  // would 404 against PortalOrg.librechat_container and silently drop every
  // feedback event -- the exact "wired but still dead" failure mode this
  // fix exists to close.
  // Raw entrypoint source: the tenant-id expression lives inside the outer
  // REPLACE template literal, so its own backtick/${} are backslash-escaped
  // in source (unescaped only after Node evaluates REPLACE at patch time).
  assert.match(
    target,
    /librechat_tenant_id: process\.env\.KLAI_ORG_SLUG \? \\`librechat-\\\$\{process\.env\.KLAI_ORG_SLUG\}\\` : null,/,
    name,
  );
  assert.doesNotMatch(target, /req(\.|\?\.)user(\.|\?\.)tenantId/, name);
}

// The transform block must be byte-identical between the two entrypoints --
// same drift-prevention contract as the Meili block above it.
function extractFeedbackBlock(source) {
  const match = source.match(/node <<'KB_FEEDBACK_NODE'\n([\s\S]*?)\nKB_FEEDBACK_NODE\nfi/);
  assert.ok(match, 'feedback transform heredoc not found');
  return match[1];
}
const klaiFeedbackBlock = extractFeedbackBlock(entrypoint);
const getklaiFeedbackBlock = extractFeedbackBlock(getklaiEntrypoint);
assert.equal(
  klaiFeedbackBlock,
  getklaiFeedbackBlock,
  'feedback transform block drifted between klai-entrypoint.sh and getklai/entrypoint.sh',
);

// --- Env wiring: both deployment paths must supply PORTAL_INTERNAL_URL /
// PORTAL_INTERNAL_SECRET / KLAI_ORG_SLUG, or the wired patch is a no-op. ---
assert.match(dockerCompose, /KLAI_ORG_SLUG: getklai/);
assert.match(dockerCompose, /PORTAL_INTERNAL_URL: http:\/\/portal-api:8010/);
assert.match(dockerCompose, /PORTAL_INTERNAL_SECRET: \$\{PORTAL_API_INTERNAL_SECRET\}/);

// Since #887 the value lives in _reconcilable_env_vars() (single source of
// truth, additively reconciled onto existing tenants) and is rendered into the
// generated env by reconcilable_env_lines. Pin both halves so a refactor that
// drops either one still fails here.
assert.match(generator, /"PORTAL_INTERNAL_URL": "http:\/\/portal-api:8010"/);
assert.match(generator, /reconcilable_env_lines/);
assert.match(generator, /"PORTAL_INTERNAL_SECRET": settings\.internal_secret/);
// KLAI_ORG_SLUG={slug} already exists for every tenant env (Meili wiring
// depends on it too) -- assert it's still there so the feedback patch's
// tenant-id derivation keeps a value to read.
assert.match(generator, /KLAI_ORG_SLUG=\{slug\}/);

// --- The receiving portal-api endpoint the patch calls must still exist
// and accept exactly the fields the patch sends. ---
assert.match(internalApi, /@router\.post\("\/v1\/kb-feedback"/);
assert.match(internalApi, /class KbFeedbackIn\(BaseModel\):/);
for (const field of [
  'conversation_id',
  'message_id',
  'message_created_at',
  'rating',
  'librechat_user_id',
  'librechat_tenant_id',
]) {
  assert.match(internalApi, new RegExp(`${field}:`), `KbFeedbackIn must still declare ${field}`);
}

// --- Execute the actual transform (extracted from klai-entrypoint.sh, not a
// second copy) against fixtures, mirroring the runMeiliPatch helper in
// meili_tenant_indexes.test.cjs. ---
function runFeedbackPatch(messagesJsContent) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'klai-feedback-entrypoint-'));
  const target = path.join(tmp, 'app/api/server/routes/messages.js');
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, messagesJsContent);

  const script = klaiFeedbackBlock.replaceAll(
    '/app/api/server/routes/messages.js',
    target,
  );

  return {
    tmp,
    target,
    result: spawnSync(process.execPath, ['-e', script], { encoding: 'utf8' }),
  };
}

const FIXTURE_MESSAGES_JS = `router.put('/:conversationId/:messageId/feedback', validateMessageReq, async (req, res) => {
  try {
    const { conversationId, messageId } = req.params;
    const { feedback } = req.body;

    const updatedMessage = await db.updateMessage(
      req?.user?.id,
      {
        messageId,
        feedback: feedback || null,
      },
      { context: 'updateFeedback' },
    );

    // Best-effort: Assistants messages do not have deterministic AgentRun traces.
    if (!isAssistantsEndpoint(updatedMessage.endpoint)) {
      sendFeedbackScore({
        traceId: traceIdForMessage(messageId),
        feedback: updatedMessage.feedback,
        metadata: {
          messageId: updatedMessage.messageId ?? messageId,
          parentMessageId: updatedMessage.parentMessageId,
          conversationId: updatedMessage.conversationId ?? conversationId,
          sessionId: updatedMessage.conversationId ?? conversationId,
          userId: req?.user?.id,
          endpoint: updatedMessage.endpoint,
          sender: updatedMessage.sender,
          isCreatedByUser: updatedMessage.isCreatedByUser,
          tokenCount: updatedMessage.tokenCount,
        },
      }).catch((err) => logger.error('[langfuse] feedback score failed:', err));
    }

    res.json({
      messageId,
      conversationId,
      feedback: updatedMessage.feedback,
    });
  } catch (error) {
    logger.error('Error updating message feedback:', error);
    res.status(500).json({ error: 'Failed to update feedback' });
  }
});
`;

// Fixture mirrors the real shape confirmed against
// ghcr.io/danny-avila/librechat:v0.8.7 (verified 2026-08-13: db.updateMessage
// signature and the post-v0.8.6 langfuse sendFeedbackScore() call are both
// present in the FIND anchor already).
const patched = runFeedbackPatch(FIXTURE_MESSAGES_JS);
assert.equal(patched.result.status, 0, patched.result.stderr);
const patchedContent = fs.readFileSync(patched.target, 'utf8');
assert.match(patchedContent, /SPEC-KB-015: Forward feedback to portal-api/);
assert.match(patchedContent, /internal\/v1\/kb-feedback/);
assert.match(
  patchedContent,
  /librechat_tenant_id: process\.env\.KLAI_ORG_SLUG \? `librechat-\$\{process\.env\.KLAI_ORG_SLUG\}` : null,/,
);
// The langfuse block from upstream must survive untouched -- REQ-KB-015
// forwarding is additive, never a replacement of upstream behaviour.
assert.match(patchedContent, /sendFeedbackScore\(\{/);
assert.match(patchedContent, /\[langfuse\] feedback score failed/);

const checkResult = spawnSync(process.execPath, ['--check', patched.target], { encoding: 'utf8' });
assert.equal(checkResult.status, 0, checkResult.stderr);

// --- Fail-loud: anchor missing (simulates an upstream LibreChat release
// that reshaped the feedback route). ---
const driftedFixture = FIXTURE_MESSAGES_JS.replace(
  "{ context: 'updateFeedback' }",
  "{ context: 'updateFeedbackRenamed' }",
);
const drifted = runFeedbackPatch(driftedFixture);
assert.notEqual(drifted.result.status, 0);
assert.match(
  drifted.result.stderr,
  /could not apply SPEC-KB-015 feedback-forward patch/,
);
assert.match(drifted.result.stderr, /anchor not found/);

// --- Fail-loud: target file missing entirely. ---
const missingTmp = fs.mkdtempSync(path.join(os.tmpdir(), 'klai-feedback-missing-'));
const missingTarget = path.join(missingTmp, 'app/api/server/routes/messages.js');
const missingScript = klaiFeedbackBlock.replaceAll(
  '/app/api/server/routes/messages.js',
  missingTarget,
);
const missingResult = spawnSync(process.execPath, ['-e', missingScript], { encoding: 'utf8' });
assert.notEqual(missingResult.status, 0);
assert.match(missingResult.stderr, /required SPEC-KB-015 feedback-forward patch target is missing/);

console.log('OK: SPEC-KB-015 feedback forwarding is wired into both live LibreChat entrypoints and fails loud on anchor drift.');
