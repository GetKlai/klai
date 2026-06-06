const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const repoRoot = path.resolve(__dirname, '../../..');
const patcher = path.join(repoRoot, 'deploy/librechat/getklai/apply-canary-config.py');
const compose = fs.readFileSync(path.join(repoRoot, 'deploy/docker-compose.yml'), 'utf8');
const workflow = fs.readFileSync(path.join(repoRoot, '.github/workflows/deploy-compose.yml'), 'utf8');
const entrypoint = fs.readFileSync(path.join(repoRoot, 'deploy/librechat/getklai/entrypoint.sh'), 'utf8');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'getklai-librechat-'));
const configPath = path.join(tmp, 'librechat.yaml');
fs.writeFileSync(
  configPath,
  `version: 1.3.8

interface:
  runCode: false
  artifacts: false

endpoints:
  openAI:
    disabled: true
  agents:
    capabilities:
      - 'execute_code'
      - 'artifacts'
      - 'skills'
      - 'subagents'
  custom:
    - name: "Klai AI"
`,
);

assert.equal(execFileSync('python3', [patcher, configPath], { encoding: 'utf8' }).trim(), 'changed');
const patched = fs.readFileSync(configPath, 'utf8');
assert.match(patched, /^version: 1\.3\.12$/m);
assert.match(
  patched,
  /endpoints:\n  openAI:\n    disabled: true\n  agents:\n    capabilities:\n      - 'deferred_tools'\n      - 'web_search'\n      - 'artifacts'\n      - 'ocr'\n      - 'tools'\n  custom:/,
);
const capabilitiesBlock = patched.match(/  agents:\n    capabilities:\n(?:      - .+\n)+/)?.[0] ?? '';
assert.doesNotMatch(
  capabilitiesBlock,
  /execute_code|skills|subagents|file_search|context|chain/,
);
assert.equal(execFileSync('python3', [patcher, configPath], { encoding: 'utf8' }).trim(), 'unchanged');

assert.match(
  compose,
  /\.\/librechat\/getklai\/entrypoint\.sh:\/klai-entrypoint\.sh:ro/,
);
assert.match(compose, /CUSTOM_FOOTER: ""/);
assert.match(workflow, /deploy\/librechat\/getklai\/entrypoint\.sh/);
assert.match(workflow, /apply-canary-config\.py \/opt\/klai\/librechat\/getklai\/librechat\.yaml/);
assert.match(workflow, /clear_librechat_config_cache "configs:\*"/);
assert.match(workflow, /force-recreating librechat-getklai/);
assert.match(entrypoint, /klai-hide-librechat-footer-v1/);
assert.match(entrypoint, /\[role="contentinfo"\]\{display:none!important\}/);

console.log('OK: getklai LibreChat canary config disables risky v0.8.6 capabilities and hides footer.');
