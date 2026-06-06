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

assert.match(dockerCompose, /image: getmeili\/meilisearch:v1\.45\.2/);
assert.match(dockerCompose, /MEILI_DB_PATH: \/meili_data/);
assert.match(dockerCompose, /MEILI_MASTER_KEY: "\$\{GETKLAI_MEILI_API_KEY:\?set a Meili key scoped to getklai_messages,getklai_convos\}"/);
assert.match(dockerCompose, /MEILI_MESSAGES_INDEX: getklai_messages/);
assert.match(dockerCompose, /MEILI_CONVOS_INDEX: getklai_convos/);
assert.match(devCompose, /image: getmeili\/meilisearch:v1\.45\.2/);
assert.match(devCompose, /MEILI_DB_PATH: \/meili_data/);

const missingEnv = spawnSync('sh', [path.join(repoRoot, 'deploy/librechat/klai-entrypoint.sh')], {
  cwd: repoRoot,
  env: { SEARCH: 'true' },
  encoding: 'utf8',
});
assert.notEqual(missingEnv.status, 0);
assert.match(missingEnv.stderr, /SEARCH=true requires MEILI_MESSAGES_INDEX and MEILI_CONVOS_INDEX/);

const meiliPatchBlock = entrypoint.match(/node <<'NODE'\n([\s\S]*?)\nNODE\nfi/)[1];

function runMeiliPatch(fixtures) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'klai-meili-entrypoint-'));
  for (const [filePath, content] of Object.entries(fixtures)) {
    const target = path.join(tmp, filePath);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content);
  }

  let script = meiliPatchBlock;
  for (const runtimePath of [
    '/app/packages/data-schemas/dist/models/message.cjs',
    '/app/packages/data-schemas/dist/models/convo.cjs',
    '/app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs',
    '/app/api/db/indexSync.js',
  ]) {
    script = script.replaceAll(runtimePath, path.join(tmp, runtimePath.slice(1)));
  }

  return {
    tmp,
    result: spawnSync(process.execPath, ['-e', script], { encoding: 'utf8' }),
  };
}

const patched = runMeiliPatch({
  'app/packages/data-schemas/dist/models/message.cjs': "module.exports = { indexName: 'messages' };\n",
  'app/packages/data-schemas/dist/models/convo.cjs': "module.exports = { indexName: 'convos' };\n",
  'app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs': "client.index('messages');\nclient.index('convos');\n",
  'app/api/db/indexSync.js': "client.index('messages');\nclient.index('convos');\n",
});
assert.equal(patched.result.status, 0, patched.result.stderr);
assert.doesNotMatch(
  fs.readFileSync(path.join(patched.tmp, 'app/api/db/indexSync.js'), 'utf8'),
  /client\.index\(['"](messages|convos)['"]\)/,
);

const mixed = runMeiliPatch({
  'app/packages/data-schemas/dist/models/message.cjs': "module.exports = { indexName: process.env.MEILI_MESSAGES_INDEX || 'messages' };\n",
  'app/packages/data-schemas/dist/models/convo.cjs': "module.exports = { indexName: process.env.MEILI_CONVOS_INDEX || 'convos' };\n",
  'app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs': "client.index(process.env.MEILI_MESSAGES_INDEX || 'messages');\nclient.index('messages');\nclient.index('convos');\n",
  'app/api/db/indexSync.js': "client.index(process.env.MEILI_MESSAGES_INDEX || 'messages');\nclient.index('messages');\nclient.index('convos');\n",
});
assert.notEqual(mixed.result.status, 0);
assert.match(mixed.result.stderr, /unsafe global Meili reference remains/);

console.log('OK: LibreChat Meili search is wired to tenant-scoped indexes.');
