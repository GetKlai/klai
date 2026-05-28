const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '../../..');
const streamServicesPatch = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/patches/createStreamServices.ts'),
  'utf8',
);
const manifest = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/patch-manifest.txt'),
  'utf8',
);
const dockerCompose = fs.readFileSync(path.join(repoRoot, 'deploy/docker-compose.yml'), 'utf8');
const provisioning = fs.readFileSync(
  path.join(repoRoot, 'klai-portal/backend/app/services/provisioning/infrastructure.py'),
  'utf8',
);

assert.match(streamServicesPatch, /const CLEANUP_ON_COMPLETE = false;/);
assert.match(streamServicesPatch, /cleanupOnComplete: CLEANUP_ON_COMPLETE,/);
assert.match(
  manifest,
  /patches\/createStreamServices\.ts\|\/app\/packages\/api\/src\/stream\/createStreamServices\.ts\|1455df2b1671a60be4fa484c867f896cd67b7a6a729224ef4bc7622af704698c\|/,
);
assert.match(
  dockerCompose,
  /\.\/librechat\/patches\/createStreamServices\.ts:\/app\/packages\/api\/src\/stream\/createStreamServices\.ts:ro/,
);
assert.match(
  provisioning,
  /"patches\/createStreamServices\.ts": "\/app\/packages\/api\/src\/stream\/createStreamServices\.ts"/,
);

console.log('OK: LibreChat stream services patch keeps completed jobs for late SSE subscribers.');
