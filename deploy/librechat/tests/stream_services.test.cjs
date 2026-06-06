const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '../../..');
const streamServicesPatch = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/patches/createStreamServices.ts'),
  'utf8',
);
const getklaiStreamServicesPatch = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/getklai/patches/createStreamServices.ts'),
  'utf8',
);
const manifest = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/patch-manifest.txt'),
  'utf8',
);
const getklaiManifest = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/getklai/patch-manifest.txt'),
  'utf8',
);
const dockerCompose = fs.readFileSync(path.join(repoRoot, 'deploy/docker-compose.yml'), 'utf8');
const provisioning = fs.readFileSync(
  path.join(repoRoot, 'klai-portal/backend/app/services/provisioning/infrastructure.py'),
  'utf8',
);

assert.match(streamServicesPatch, /const CLEANUP_ON_COMPLETE = false;/);
assert.match(streamServicesPatch, /cleanupOnComplete: CLEANUP_ON_COMPLETE,/);
assert.match(getklaiStreamServicesPatch, /const CLEANUP_ON_COMPLETE = false;/);
assert.match(getklaiStreamServicesPatch, /cleanupOnComplete: CLEANUP_ON_COMPLETE,/);
assert.match(
  manifest,
  /patches\/createStreamServices\.ts\|\/app\/packages\/api\/src\/stream\/createStreamServices\.ts\|ac039dc98dca672b024e87ea92a4ffb0ed3287b3db8e7e59a46f4ba8fa89ffb3\|/,
);
assert.match(
  getklaiManifest,
  /getklai\/patches\/createStreamServices\.ts\|\/app\/packages\/api\/src\/stream\/createStreamServices\.ts\|ac039dc98dca672b024e87ea92a4ffb0ed3287b3db8e7e59a46f4ba8fa89ffb3\|/,
);
assert.match(
  dockerCompose,
  /\.\/librechat\/getklai\/patches\/createStreamServices\.ts:\/app\/packages\/api\/src\/stream\/createStreamServices\.ts:ro/,
);
assert.match(
  provisioning,
  /"patches\/createStreamServices\.ts": "\/app\/packages\/api\/src\/stream\/createStreamServices\.ts"/,
);

console.log('OK: LibreChat stream services patch keeps completed jobs for late SSE subscribers.');
