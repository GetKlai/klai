const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '../../..');
const script = path.join(repoRoot, 'deploy/librechat/klai-entrypoint.sh');
const yamlPath = path.join(repoRoot, 'deploy/librechat/librechat.yaml');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'klai-entrypoint-'));
const index = path.join(tmp, 'index.html');

fs.writeFileSync(
  index,
  '<!doctype html><html><head></head><body><footer><a href="https://librechat.ai/">LibreChat v0.8.6</a> - Every AI for Everyone.</footer></body></html>',
);

const runEntrypoint = () =>
  spawnSync('sh', [script, 'true'], {
    cwd: repoRoot,
    env: { ...process.env, KLAI_LIBRECHAT_INDEX: index },
    encoding: 'utf8',
  });

const first = runEntrypoint();
assert.equal(first.status, 0, first.stderr || first.stdout);

const injected = fs.readFileSync(index, 'utf8');
assert.match(injected, /klai-force-light/);
assert.match(injected, /klai-kb-disclosure-v7/);
assert.match(injected, /klai-hide-librechat-footer-v1/);

const second = runEntrypoint();
assert.equal(second.status, 0, second.stderr || second.stdout);
assert.equal(fs.readFileSync(index, 'utf8'), injected);

const yaml = fs.readFileSync(yamlPath, 'utf8');
const capabilitiesBlock = yaml.match(/  agents:\n    capabilities:\n((?:      - .+\n)+)/);
assert.ok(capabilitiesBlock, 'expected endpoints.agents.capabilities in librechat.yaml');
assert.ok(!capabilitiesBlock[1].includes('- skills\n'), 'skills capability must stay disabled');

console.log('OK: Klai LibreChat client polish injects footer hide and disables Skills.');
