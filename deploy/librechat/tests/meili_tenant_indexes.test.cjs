const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

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
const devCompose = fs.readFileSync(path.join(repoRoot, 'docker-compose.dev.yml'), 'utf8');
const generator = fs.readFileSync(
  path.join(repoRoot, 'klai-portal/backend/app/services/provisioning/generators.py'),
  'utf8',
);
const deprovisioning = fs.readFileSync(
  path.join(repoRoot, 'klai-portal/backend/app/services/provisioning/deprovisioning_steps.py'),
  'utf8',
);

assert.match(generator, /MEILI_MESSAGES_INDEX=\{slug\}_messages/);
assert.match(generator, /MEILI_CONVOS_INDEX=\{slug\}_convos/);
assert.match(generator, /MEILI_NO_SYNC=true/);
assert.match(generator, /meili_api_key: str,/);
assert.match(generator, /meili_api_key is required; never write the Meili master key into tenant envs/);
assert.doesNotMatch(generator, /settings\.meili_master_key/);
assert.doesNotMatch(generator, /^SEARCH=true$/m);

assert.match(deprovisioning, /f"\{state\.slug\}_messages"/);
assert.match(deprovisioning, /f"\{state\.slug\}_convos"/);
assert.doesNotMatch(deprovisioning, /delete\(f"\/indexes\/\{state\.slug\}"\)/);

assert.match(entrypoint, /MEILI_MESSAGES_INDEX/);
assert.match(entrypoint, /MEILI_CONVOS_INDEX/);
assert.match(entrypoint, /SEARCH=true requires MEILI_MESSAGES_INDEX and MEILI_CONVOS_INDEX/);
assert.match(entrypoint, /refusing unsafe global Meili indexes/);
assert.match(entrypoint, /\/app\/packages\/data-schemas\/dist\/models\/message\.cjs/);
assert.match(entrypoint, /\/app\/packages\/data-schemas\/dist\/models\/convo\.cjs/);
assert.match(entrypoint, /\/app\/packages\/data-schemas\/dist\/models\/plugins\/mongoMeili\.cjs/);
assert.match(entrypoint, /\/app\/api\/db\/indexSync\.js/);
assert.match(entrypoint, /throw new Error\(`\[klai-entrypoint\] could not apply/);
assert.match(entrypoint, /unsafe global Meili reference remains/);
assert.match(entrypoint, /indexName:\\s\*\['"\]messages\['"\]/);
assert.match(entrypoint, /client\\\.index\\\(\['"\]messages\['"\]\\\)/);
assert.match(entrypoint, /process\.env\.MEILI_MESSAGES_INDEX \|\| 'messages'/);
assert.match(entrypoint, /process\.env\.MEILI_CONVOS_INDEX \|\| 'convos'/);

// v0.8.7 rolldown-bundled shape support (2026-08-13 retarget): the entrypoint
// must know about the single-file bundle AND fail loud (not silently guess)
// when neither the pre-rolldown per-model files nor the bundle are present,
// or when only some of the three pre-rolldown files are present.
assert.match(entrypoint, /\/app\/packages\/data-schemas\/dist\/index\.cjs/);
assert.match(entrypoint, /legacyPresent\.length === legacyPathList\.length/);
assert.match(entrypoint, /legacyPresent\.length === 0 && bundledPresent/);
assert.match(entrypoint, /ambiguous LibreChat data-schemas dist shape/);
assert.match(
  entrypoint,
  /required LibreChat Meili patch target is missing: neither pre-rolldown per-model files/,
);

for (const target of [entrypoint, getklaiEntrypoint]) {
  assert.match(target, /MEILI_MESSAGES_INDEX/);
  assert.match(target, /MEILI_CONVOS_INDEX/);
  assert.match(target, /SEARCH=true requires MEILI_MESSAGES_INDEX and MEILI_CONVOS_INDEX/);
  assert.match(target, /unsafe global Meili reference remains/);
  assert.match(target, /\/app\/packages\/data-schemas\/dist\/models\/message\.cjs/);
  assert.match(target, /\/app\/packages\/data-schemas\/dist\/models\/convo\.cjs/);
  assert.match(target, /\/app\/packages\/data-schemas\/dist\/models\/plugins\/mongoMeili\.cjs/);
  assert.match(target, /\/app\/api\/db\/indexSync\.js/);
  assert.match(target, /\/app\/packages\/data-schemas\/dist\/index\.cjs/);
  assert.match(target, /ambiguous LibreChat data-schemas dist shape/);
}

assert.match(dockerCompose, /image: getmeili\/meilisearch:v1\.53\.0/);
assert.match(dockerCompose, /MEILI_DB_PATH: \/meili_data/);
assert.match(dockerCompose, /MEILI_MASTER_KEY: "\$\{GETKLAI_MEILI_API_KEY:\?set a Meili key scoped to getklai_messages,getklai_convos\}"/);
assert.match(dockerCompose, /MEILI_MESSAGES_INDEX: getklai_messages/);
assert.match(dockerCompose, /MEILI_CONVOS_INDEX: getklai_convos/);
assert.match(dockerCompose, /ghcr\.io\/danny-avila\/librechat:v0\.8\.7/);
assert.match(dockerCompose, /librechat\/getklai\/entrypoint\.sh:\/klai-entrypoint\.sh:ro/);
assert.match(devCompose, /image: getmeili\/meilisearch:v1\.53\.0/);
assert.match(devCompose, /MEILI_DB_PATH: \/meili_data/);

const missingEnv = spawnSync('sh', [path.join(repoRoot, 'deploy/librechat/klai-entrypoint.sh')], {
  cwd: repoRoot,
  env: { SEARCH: 'true' },
  encoding: 'utf8',
});
assert.notEqual(missingEnv.status, 0);
assert.match(missingEnv.stderr, /SEARCH=true requires MEILI_MESSAGES_INDEX and MEILI_CONVOS_INDEX/);

const meiliPatchBlock = entrypoint.match(/node <<'NODE'\n([\s\S]*?)\nNODE\nfi/)[1];

// All runtime paths the patch block can reference, across both LibreChat
// data-schemas shapes (pre-rolldown per-model files and the v0.8.7+
// rolldown-bundled dist/index.cjs), plus the shape-independent indexSync.js
// target. runMeiliPatch remaps every one of these into a tmp dir so the
// script's existsSync()-based shape detection sees exactly the fixtures the
// test provided — nothing more.
const ALL_RUNTIME_PATHS = [
  '/app/packages/data-schemas/dist/models/message.cjs',
  '/app/packages/data-schemas/dist/models/convo.cjs',
  '/app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs',
  '/app/packages/data-schemas/dist/index.cjs',
  '/app/api/db/indexSync.js',
];

function runMeiliPatch(fixtures) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'klai-meili-entrypoint-'));
  for (const [filePath, content] of Object.entries(fixtures)) {
    const target = path.join(tmp, filePath);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content);
  }

  let script = meiliPatchBlock;
  for (const runtimePath of ALL_RUNTIME_PATHS) {
    script = script.replaceAll(runtimePath, path.join(tmp, runtimePath.slice(1)));
  }

  return {
    tmp,
    result: spawnSync(process.execPath, ['-e', script], { encoding: 'utf8' }),
  };
}

// --- Pre-rolldown shape (<= v0.8.6): three separate per-model files. ---

const legacyPatched = runMeiliPatch({
  'app/packages/data-schemas/dist/models/message.cjs': "module.exports = { indexName: 'messages' };\n",
  'app/packages/data-schemas/dist/models/convo.cjs': "module.exports = { indexName: 'convos' };\n",
  'app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs': "client.index('messages');\nclient.index('convos');\n",
  'app/api/db/indexSync.js': "client.index('messages');\nclient.index('convos');\n",
});
assert.equal(legacyPatched.result.status, 0, legacyPatched.result.stderr);
assert.doesNotMatch(
  fs.readFileSync(path.join(legacyPatched.tmp, 'app/api/db/indexSync.js'), 'utf8'),
  /client\.index\(['"](messages|convos)['"]\)/,
);
assert.match(
  fs.readFileSync(
    path.join(legacyPatched.tmp, 'app/packages/data-schemas/dist/models/message.cjs'),
    'utf8',
  ),
  /indexName: process\.env\.MEILI_MESSAGES_INDEX \|\| 'messages'/,
);

// Re-running against already-patched legacy fixtures must be a no-op (idempotent).
const legacyRerun = runMeiliPatch({
  'app/packages/data-schemas/dist/models/message.cjs': fs.readFileSync(
    path.join(legacyPatched.tmp, 'app/packages/data-schemas/dist/models/message.cjs'),
    'utf8',
  ),
  'app/packages/data-schemas/dist/models/convo.cjs': fs.readFileSync(
    path.join(legacyPatched.tmp, 'app/packages/data-schemas/dist/models/convo.cjs'),
    'utf8',
  ),
  'app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs': fs.readFileSync(
    path.join(legacyPatched.tmp, 'app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs'),
    'utf8',
  ),
  'app/api/db/indexSync.js': fs.readFileSync(
    path.join(legacyPatched.tmp, 'app/api/db/indexSync.js'),
    'utf8',
  ),
});
assert.equal(legacyRerun.result.status, 0, legacyRerun.result.stderr);
assert.equal(legacyRerun.result.stdout, '');

const legacyMixed = runMeiliPatch({
  'app/packages/data-schemas/dist/models/message.cjs': "module.exports = { indexName: process.env.MEILI_MESSAGES_INDEX || 'messages' };\n",
  'app/packages/data-schemas/dist/models/convo.cjs': "module.exports = { indexName: process.env.MEILI_CONVOS_INDEX || 'convos' };\n",
  'app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs': "client.index(process.env.MEILI_MESSAGES_INDEX || 'messages');\nclient.index('messages');\nclient.index('convos');\n",
  'app/api/db/indexSync.js': "client.index(process.env.MEILI_MESSAGES_INDEX || 'messages');\nclient.index('messages');\nclient.index('convos');\n",
});
assert.notEqual(legacyMixed.result.status, 0);
assert.match(legacyMixed.result.stderr, /unsafe global Meili reference remains/);

// --- Rolldown-bundled shape (>= v0.8.7): single dist/index.cjs. ---
// Fixture mirrors the real shape confirmed against
// ghcr.io/danny-avila/librechat:v0.8.7 (double-quoted indexName/client.index
// call sites; two client.index('convos') call sites, one client.index('messages')).

const BUNDLED_FIXTURE = `
function mongoMeili(schema, options) {
	const { indexName } = options;
	const index = client.index(indexName);
	schema.pre("deleteMany", async function (next) {
		const convoIndex = client.index("convos");
		const messageIndex = client.index("messages");
	});
	schema.post("findOneAndUpdate", async function (doc, next) {
		meiliDoc = await client.index("convos").getDocument(doc.conversationId);
	});
}
function createConversationModel(mongoose) {
	convoSchema.plugin(mongoMeili, {
		indexName: "convos",
		primaryKey: "conversationId"
	});
}
function createMessageModel(mongoose) {
	messageSchema.plugin(mongoMeili, {
		indexName: "messages",
		primaryKey: "messageId"
	});
}
`;

const bundledPatched = runMeiliPatch({
  'app/packages/data-schemas/dist/index.cjs': BUNDLED_FIXTURE,
  'app/api/db/indexSync.js': "client.index('messages');\nclient.index('convos');\n",
});
assert.equal(bundledPatched.result.status, 0, bundledPatched.result.stderr);
const bundledPatchedContent = fs.readFileSync(
  path.join(bundledPatched.tmp, 'app/packages/data-schemas/dist/index.cjs'),
  'utf8',
);
assert.doesNotMatch(bundledPatchedContent, /indexName: "messages"/);
assert.doesNotMatch(bundledPatchedContent, /indexName: "convos"/);
assert.doesNotMatch(bundledPatchedContent, /client\.index\("messages"\)/);
assert.doesNotMatch(bundledPatchedContent, /client\.index\("convos"\)/);
assert.match(bundledPatchedContent, /indexName: process\.env\.MEILI_MESSAGES_INDEX \|\| 'messages'/);
assert.match(bundledPatchedContent, /indexName: process\.env\.MEILI_CONVOS_INDEX \|\| 'convos'/);
// mongoMeili's deleteMany hook has 2 client.index('convos') call sites plus
// findOneAndUpdate's — all three must be rewritten (global-flag replace).
assert.equal(
  (bundledPatchedContent.match(/client\.index\(process\.env\.MEILI_CONVOS_INDEX \|\| 'convos'\)/g) || [])
    .length,
  2,
);
assert.match(
  bundledPatchedContent,
  /client\.index\(process\.env\.MEILI_MESSAGES_INDEX \|\| 'messages'\)/,
);

// Re-running against already-patched bundled fixture must be a no-op (idempotent).
const bundledRerun = runMeiliPatch({
  'app/packages/data-schemas/dist/index.cjs': bundledPatchedContent,
  'app/api/db/indexSync.js': fs.readFileSync(
    path.join(bundledPatched.tmp, 'app/api/db/indexSync.js'),
    'utf8',
  ),
});
assert.equal(bundledRerun.result.status, 0, bundledRerun.result.stderr);
assert.equal(bundledRerun.result.stdout, '');

const bundledMixed = runMeiliPatch({
  'app/packages/data-schemas/dist/index.cjs': BUNDLED_FIXTURE.replace(
    'client.index("messages");',
    "client.index(process.env.MEILI_MESSAGES_INDEX || 'messages');\n\t\tclient.index(\"messages\");",
  ),
  'app/api/db/indexSync.js': "client.index('messages');\nclient.index('convos');\n",
});
assert.notEqual(bundledMixed.result.status, 0);
assert.match(bundledMixed.result.stderr, /unsafe global Meili reference remains/);

// --- Fail-loud: neither shape present. ---

const missingShape = runMeiliPatch({
  'app/api/db/indexSync.js': "client.index('messages');\nclient.index('convos');\n",
});
assert.notEqual(missingShape.result.status, 0);
assert.match(
  missingShape.result.stderr,
  /required LibreChat Meili patch target is missing: neither pre-rolldown per-model files/,
);

// --- Fail-loud: ambiguous partial legacy shape (some but not all per-model files). ---

const partialShape = runMeiliPatch({
  'app/packages/data-schemas/dist/models/message.cjs': "module.exports = { indexName: 'messages' };\n",
  'app/api/db/indexSync.js': "client.index('messages');\nclient.index('convos');\n",
});
assert.notEqual(partialShape.result.status, 0);
assert.match(partialShape.result.stderr, /ambiguous LibreChat data-schemas dist shape/);

// --- Fail-loud: indexSync.js missing regardless of which model shape is present. ---

const missingIndexSync = runMeiliPatch({
  'app/packages/data-schemas/dist/index.cjs': BUNDLED_FIXTURE,
});
assert.notEqual(missingIndexSync.result.status, 0);
assert.match(
  missingIndexSync.result.stderr,
  /required LibreChat Meili patch target is missing: .*indexSync\.js/,
);

console.log('OK: LibreChat Meili search is wired to tenant-scoped indexes across both data-schemas shapes.');
