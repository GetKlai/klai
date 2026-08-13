const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// Self-contained (no docker/network) regression coverage for
// deploy/librechat/dry-run-transforms.cjs's own control flow: sync-guard
// detection and pass/fail wiring. The full docker-backed exercise (against
// files actually extracted from ghcr.io/danny-avila/librechat:v0.8.7) is run
// by deploy/librechat/check-patch-drift.sh in CI and was verified manually
// for this change -- see the PR/commit description for the positive and
// negative-check transcripts.

const repoRoot = path.resolve(__dirname, '../../..');
const dryRunScript = path.join(repoRoot, 'deploy/librechat/dry-run-transforms.cjs');
const realKlaiEntrypoint = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/klai-entrypoint.sh'),
  'utf8',
);

function extractBlock(source, re) {
  const m = source.match(re);
  assert.ok(m, 'fixture setup: could not extract block from real entrypoint');
  return m[1];
}

const MEILI_RE = /node <<'NODE'\n([\s\S]*?)\nNODE\nfi/;
const FEEDBACK_RE = /node <<'KB_FEEDBACK_NODE'\n([\s\S]*?)\nKB_FEEDBACK_NODE\nfi/;
const realMeiliBlock = extractBlock(realKlaiEntrypoint, MEILI_RE);
const realFeedbackBlock = extractBlock(realKlaiEntrypoint, FEEDBACK_RE);

function writeFakeEntrypoint(dir, name, { meiliBlock, feedbackBlock }) {
  const content = [
    '#!/bin/sh',
    'set -e',
    '',
    "node <<'NODE'",
    meiliBlock,
    'NODE',
    'fi',
    '',
    "node <<'KB_FEEDBACK_NODE'",
    feedbackBlock,
    'KB_FEEDBACK_NODE',
    'fi',
    '',
  ].join('\n');
  const p = path.join(dir, name);
  fs.writeFileSync(p, content);
  return p;
}

function writeExtractedFixtures(dir, { messagesJs } = {}) {
  const bundledPath = path.join(dir, 'app/packages/data-schemas/dist/index.cjs');
  const indexSyncPath = path.join(dir, 'app/api/db/indexSync.js');
  const messagesPath = path.join(dir, 'app/api/server/routes/messages.js');
  fs.mkdirSync(path.dirname(bundledPath), { recursive: true });
  fs.mkdirSync(path.dirname(indexSyncPath), { recursive: true });
  fs.mkdirSync(path.dirname(messagesPath), { recursive: true });
  fs.writeFileSync(
    bundledPath,
    `
function mongoMeili(schema, options) {
	const { indexName } = options;
	const index = client.index(indexName);
	schema.pre("deleteMany", async function (next) {
		const convoIndex = client.index("convos");
		const messageIndex = client.index("messages");
	});
}
function createConversationModel(mongoose) {
	convoSchema.plugin(mongoMeili, { indexName: "convos", primaryKey: "conversationId" });
}
function createMessageModel(mongoose) {
	messageSchema.plugin(mongoMeili, { indexName: "messages", primaryKey: "messageId" });
}
`,
  );
  fs.writeFileSync(indexSyncPath, "client.index('messages');\nclient.index('convos');\n");
  fs.writeFileSync(
    messagesPath,
    messagesJs ??
      `router.put('/:conversationId/:messageId/feedback', validateMessageReq, async (req, res) => {
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
`,
  );
  return { bundledPath, indexSyncPath, messagesPath };
}

function runDryRun(extractedRoot, klaiPath, getklaiPath) {
  return spawnSync(
    process.execPath,
    [dryRunScript, extractedRoot, klaiPath, getklaiPath],
    { encoding: 'utf8' },
  );
}

// --- Happy path: identical entrypoints, valid extracted fixtures. ---
{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dry-run-happy-'));
  const klaiPath = writeFakeEntrypoint(dir, 'klai-entrypoint.sh', {
    meiliBlock: realMeiliBlock,
    feedbackBlock: realFeedbackBlock,
  });
  const getklaiPath = writeFakeEntrypoint(dir, 'getklai-entrypoint.sh', {
    meiliBlock: realMeiliBlock,
    feedbackBlock: realFeedbackBlock,
  });
  const extractedRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'dry-run-extract-'));
  writeExtractedFixtures(extractedRoot);

  const r = runDryRun(extractedRoot, klaiPath, getklaiPath);
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /DRY-RUN OK: all runtime transforms applied cleanly/);
}

// --- Sync-guard: entrypoints drift apart. ---
{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dry-run-syncguard-'));
  const klaiPath = writeFakeEntrypoint(dir, 'klai-entrypoint.sh', {
    meiliBlock: realMeiliBlock,
    feedbackBlock: realFeedbackBlock,
  });
  const getklaiPath = writeFakeEntrypoint(dir, 'getklai-entrypoint.sh', {
    meiliBlock: realMeiliBlock,
    feedbackBlock: realFeedbackBlock.replace('SPEC-KB-015', 'SPEC-KB-015-DRIFTED'),
  });
  const extractedRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'dry-run-extract-'));
  writeExtractedFixtures(extractedRoot);

  const r = runDryRun(extractedRoot, klaiPath, getklaiPath);
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /DRY-RUN FAIL \[feedback-sync-guard\]/);
}

// --- Fail-loud: extracted messages.js has a drifted anchor. ---
{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dry-run-anchor-'));
  const klaiPath = writeFakeEntrypoint(dir, 'klai-entrypoint.sh', {
    meiliBlock: realMeiliBlock,
    feedbackBlock: realFeedbackBlock,
  });
  const getklaiPath = writeFakeEntrypoint(dir, 'getklai-entrypoint.sh', {
    meiliBlock: realMeiliBlock,
    feedbackBlock: realFeedbackBlock,
  });
  const extractedRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'dry-run-extract-'));
  writeExtractedFixtures(extractedRoot, {
    messagesJs: `router.put('/:conversationId/:messageId/feedback', validateMessageReq, async (req, res) => {
  // anchor drifted: no updateFeedback context marker
  res.json({});
});
`,
  });

  const r = runDryRun(extractedRoot, klaiPath, getklaiPath);
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /DRY-RUN FAIL \[feedback\]/);
  assert.match(r.stderr, /anchor drift/);
}

// --- Missing extraction: no runtime files at all. ---
{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dry-run-missing-'));
  const klaiPath = writeFakeEntrypoint(dir, 'klai-entrypoint.sh', {
    meiliBlock: realMeiliBlock,
    feedbackBlock: realFeedbackBlock,
  });
  const getklaiPath = writeFakeEntrypoint(dir, 'getklai-entrypoint.sh', {
    meiliBlock: realMeiliBlock,
    feedbackBlock: realFeedbackBlock,
  });
  const extractedRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'dry-run-extract-empty-'));

  const r = runDryRun(extractedRoot, klaiPath, getklaiPath);
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /DRY-RUN FAIL \[meili-shape\]/);
  assert.match(r.stderr, /DRY-RUN FAIL \[feedback\]/);
}

console.log('OK: dry-run-transforms.cjs sync-guard and fail-loud wiring behave correctly on synthetic fixtures.');
