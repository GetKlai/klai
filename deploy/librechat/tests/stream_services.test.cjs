const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// 2026-08-14: the mounted deploy/librechat/patches/createStreamServices.ts
// bind-mount over the SOURCE file
// /app/packages/api/src/stream/createStreamServices.ts was measured INERT.
// The runtime loads @librechat/api via the workspace symlink
// node_modules/@librechat/api -> packages/api, whose package.json `main`
// points at the pre-built /app/packages/api/dist/index.cjs bundle; nothing
// recompiles the mounted .ts at container start, so `cleanupOnComplete`
// never took effect and GenerationJobManager kept discarding completed jobs
// immediately (services.cleanupOnComplete ?? true). This test replaces the
// old mount-pinning coverage with coverage for the fail-loud entrypoint
// transform (SPEC-STREAM-CLEANUP-001) that patches the BUILT bundle in
// place instead, mirroring the SPEC-KB-015 feedback-forward pattern.

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
const manifest = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/patch-manifest.txt'),
  'utf8',
);
const provisioning = fs.readFileSync(
  path.join(repoRoot, 'klai-portal/backend/app/services/provisioning/infrastructure.py'),
  'utf8',
);

// --- The inert mounted-source artifacts must be gone. ---
for (const deadFile of [
  'deploy/librechat/patches/createStreamServices.ts',
  'deploy/librechat/getklai/patches/createStreamServices.ts',
]) {
  assert.equal(
    fs.existsSync(path.join(repoRoot, deadFile)),
    false,
    `${deadFile} must be deleted -- the source-file bind-mount was inert; superseded by the wired transform in klai-entrypoint.sh / getklai/entrypoint.sh`,
  );
}
assert.doesNotMatch(manifest, /createStreamServices\.ts/, 'patch-manifest.txt');
// getklai/patch-manifest.txt is gone: the canary runs the Klai-owned image
// (SPEC-LIBRECHAT-PATCH-MODEL-001 Phase 3), so its provenance comes from the
// build manifest baked into that image, not from upstream hashes of files it
// no longer mounts. Assert it stays retired rather than quietly returning.
assert.equal(
  fs.existsSync(path.join(repoRoot, 'deploy/librechat/getklai/patch-manifest.txt')),
  false,
  'getklai/patch-manifest.txt describes a mount model the canary no longer uses',
);
assert.doesNotMatch(dockerCompose, /createStreamServices\.ts/, 'docker-compose.yml');
assert.doesNotMatch(provisioning, /createStreamServices\.ts/, 'infrastructure.py');

// --- Both live entrypoints embed the fail-loud stream-cleanup transform. ---
for (const [name, target] of [
  ['deploy/librechat/klai-entrypoint.sh', entrypoint],
  ['deploy/librechat/getklai/entrypoint.sh', getklaiEntrypoint],
]) {
  assert.match(target, /SPEC-STREAM-CLEANUP-001/, name);
  assert.match(target, /STREAM_CLEANUP_TARGET=\/app\/packages\/api\/dist\/index\.cjs/, name);
  // The skip-check must recognise BOTH ways the fix can already be present:
  // this transform having run before, and an image built from the source diff
  // carrying CLEANUP_ON_COMPLETE. Matching only the first would layer a second,
  // duplicate cleanupOnComplete key on top of the baked-in one on the canary.
  assert.match(
    target,
    /grep -qE "SPEC-STREAM-CLEANUP-001\|CLEANUP_ON_COMPLETE" "\$STREAM_CLEANUP_TARGET"/,
    name,
  );
  assert.match(target, /already in place .*skipping/, name);
  assert.match(
    target,
    /required SPEC-STREAM-CLEANUP-001 patch target is missing/,
    name,
  );
  assert.match(target, /#region src\/stream\/createStreamServices\.ts/, name);
  assert.match(target, /isRedis: true/, name);
  assert.match(target, /isRedis: false/, name);
  assert.match(target, /cleanupOnComplete: false/, name);
  assert.match(
    target,
    /expected exactly 1\); LibreChat upgrade likely reshaped createStreamServices/,
    name,
  );
}

// The transform block must be byte-identical between the two entrypoints --
// same drift-prevention contract as the Meili and feedback blocks.
function extractStreamCleanupBlock(source) {
  const match = source.match(/node <<'STREAM_CLEANUP_NODE'\n([\s\S]*?)\nSTREAM_CLEANUP_NODE\nfi/);
  assert.ok(match, 'stream-cleanup transform heredoc not found');
  return match[1];
}
const klaiStreamCleanupBlock = extractStreamCleanupBlock(entrypoint);
const getklaiStreamCleanupBlock = extractStreamCleanupBlock(getklaiEntrypoint);
assert.equal(
  klaiStreamCleanupBlock,
  getklaiStreamCleanupBlock,
  'stream-cleanup transform block drifted between klai-entrypoint.sh and getklai/entrypoint.sh',
);

function assertValidNodeSyntax(script) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'klai-stream-cleanup-syntax-'));
  const p = path.join(tmp, 'block.cjs');
  fs.writeFileSync(p, script);
  const result = spawnSync(process.execPath, ['--check', p], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
}
assertValidNodeSyntax(klaiStreamCleanupBlock);

// --- Execute the actual transform (extracted from klai-entrypoint.sh, not a
// second copy) against a fixture mirroring the real bundle shape confirmed
// against ghcr.io/danny-avila/librechat:v0.8.7's
// /app/packages/api/dist/index.cjs (verified 2026-08-14: `isRedis: true` and
// `isRedis: false` each occur exactly once in the real bundle, both inside
// the //#region src/stream/createStreamServices.ts ... //#endregion block). ---
function runStreamCleanupPatch(bundleContent) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'klai-stream-cleanup-entrypoint-'));
  const target = path.join(tmp, 'app/packages/api/dist/index.cjs');
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, bundleContent);

  const script = klaiStreamCleanupBlock.replaceAll('/app/packages/api/dist/index.cjs', target);

  return {
    tmp,
    target,
    result: spawnSync(process.execPath, ['-e', script], { encoding: 'utf8' }),
  };
}

const FIXTURE_BUNDLE = `//#region src/stream/createStreamServices.ts
function createStreamServices(config = {}) {
	const useRedis = config.useRedis ?? cacheConfig.USE_REDIS_STREAMS;
	if (useRedis) try {
		const jobStore = new RedisJobStore(redisClient);
		const eventTransport = new RedisEventTransport(redisClient, subscriber);
		return {
			jobStore,
			eventTransport,
			isRedis: true
		};
	} catch (err) {
		return createInMemoryServices(inMemoryOptions);
	}
	return createInMemoryServices(inMemoryOptions);
}
function createInMemoryServices(options) {
	const jobStore = new InMemoryJobStore({
		ttlAfterComplete: options?.ttlAfterComplete ?? 3e5
	});
	const eventTransport = new InMemoryEventTransport();
	return {
		jobStore,
		eventTransport,
		isRedis: false
	};
}
//#endregion
//#region src/utils/memory.ts
const INTERVAL_MS = 6e4;
//#endregion
`;

const patched = runStreamCleanupPatch(FIXTURE_BUNDLE);
assert.equal(patched.result.status, 0, patched.result.stderr);
const patchedContent = fs.readFileSync(patched.target, 'utf8');

// Expected replacement count: exactly 2 (Redis-backed branch + in-memory
// branch), never more, never less.
const replacementCount = (patchedContent.match(/cleanupOnComplete: false/g) || []).length;
assert.equal(replacementCount, 2, 'expected exactly 2 cleanupOnComplete: false replacements');
assert.match(patchedContent, /isRedis: true, cleanupOnComplete: false/);
assert.match(patchedContent, /isRedis: false, cleanupOnComplete: false/);
// No leftover un-patched return: every isRedis literal inside the region
// must be immediately followed by the cleanupOnComplete addition.
assert.doesNotMatch(patchedContent, /isRedis: true\n/);
assert.doesNotMatch(patchedContent, /isRedis: false\n/);

const checkResult = spawnSync(process.execPath, ['--check', patched.target], { encoding: 'utf8' });
assert.equal(checkResult.status, 0, checkResult.stderr);

// --- Fail-loud: anchor missing entirely (simulates an upstream LibreChat
// release that reshaped createStreamServices so neither isRedis literal is
// present anymore). ---
const missingAnchorFixture = FIXTURE_BUNDLE.replace(/isRedis: true/, 'usesRedis: true').replace(
  /isRedis: false/,
  'usesRedis: false',
);
const missingAnchor = runStreamCleanupPatch(missingAnchorFixture);
assert.notEqual(missingAnchor.result.status, 0);
assert.match(missingAnchor.result.stderr, /SPEC-STREAM-CLEANUP-001 anchor for Redis-backed branch/);
assert.match(missingAnchor.result.stderr, /matched 0 times/);

// --- Fail-loud: anchor duplicated (simulates an ambiguous upstream reshape
// where the transform can no longer target a single, unambiguous site). ---
const duplicatedAnchorFixture = FIXTURE_BUNDLE.replace(
  '//#endregion',
  '\t// duplicate isRedis: true anchor\n//#endregion',
);
const duplicatedAnchor = runStreamCleanupPatch(duplicatedAnchorFixture);
assert.notEqual(duplicatedAnchor.result.status, 0);
assert.match(duplicatedAnchor.result.stderr, /matched 2 times/);

// --- Fail-loud: region marker itself missing (simulates the module being
// renamed or moved by an upstream release). ---
const missingRegionFixture = FIXTURE_BUNDLE.replace(
  '//#region src/stream/createStreamServices.ts',
  '//#region src/stream/renamed.ts',
);
const missingRegion = runStreamCleanupPatch(missingRegionFixture);
assert.notEqual(missingRegion.result.status, 0);
assert.match(
  missingRegion.result.stderr,
  /could not locate '\/\/#region src\/stream\/createStreamServices\.ts'/,
);

// --- Fail-loud: target file missing entirely. ---
const missingTmp = fs.mkdtempSync(path.join(os.tmpdir(), 'klai-stream-cleanup-missing-'));
const missingTarget = path.join(missingTmp, 'app/packages/api/dist/index.cjs');
const missingScript = klaiStreamCleanupBlock.replaceAll(
  '/app/packages/api/dist/index.cjs',
  missingTarget,
);
const missingResult = spawnSync(process.execPath, ['-e', missingScript], { encoding: 'utf8' });
assert.notEqual(missingResult.status, 0);
assert.match(missingResult.stderr, /required SPEC-STREAM-CLEANUP-001 patch target is missing/);

console.log(
  'OK: LibreChat stream-cleanup patch is wired into both live entrypoints (built-bundle transform, not the inert source mount) and fails loud on anchor drift.',
);
