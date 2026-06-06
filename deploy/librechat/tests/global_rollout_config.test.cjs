const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '../../..');
const config = fs.readFileSync(path.join(repoRoot, 'deploy/librechat/librechat.yaml'), 'utf8');
const entrypoint = fs.readFileSync(path.join(repoRoot, 'deploy/librechat/klai-entrypoint.sh'), 'utf8');
const drift = fs.readFileSync(path.join(repoRoot, 'deploy/librechat/check-patch-drift.sh'), 'utf8');
const portalConfig = fs.readFileSync(path.join(repoRoot, 'klai-portal/backend/app/core/config.py'), 'utf8');
const workflow = fs.readFileSync(path.join(repoRoot, '.github/workflows/deploy-librechat-config.yml'), 'utf8');

assert.match(config, /^version: 1\.3\.12$/m);
assert.match(config, /^\s{2}artifacts: true$/m);
assert.match(
  config,
  /endpoints:\n  openAI:\n    disabled: true\n  agents:\n    capabilities:\n      - 'deferred_tools'\n      - 'web_search'\n      - 'artifacts'\n      - 'ocr'\n      - 'tools'\n  assistants:/,
);

const capabilitiesBlock = config.match(/  agents:\n    capabilities:\n(?:      - .+\n)+/)?.[0] ?? '';
assert.doesNotMatch(capabilitiesBlock, /execute_code|skills|subagents|file_search|context|chain/);

assert.match(entrypoint, /klai-hide-librechat-footer-v1/);
assert.match(entrypoint, /\[role="contentinfo"\]\{display:none!important\}/);
assert.match(workflow, /deploy\/librechat\/klai-entrypoint\.sh/);
assert.match(workflow, /cp deploy\/librechat\/klai-entrypoint\.sh \/opt\/klai\/librechat\/klai-entrypoint\.sh/);
assert.match(workflow, /recreate_containers=true/);
assert.match(workflow, /timeout=600\.0/);
assert.match(drift, /LIBRECHAT_IMAGE="\$\{LIBRECHAT_IMAGE:-ghcr\.io\/danny-avila\/librechat:v0\.8\.6\}"/);
assert.match(portalConfig, /librechat_image: str = "ghcr\.io\/danny-avila\/librechat:v0\.8\.6"/);

console.log('OK: global LibreChat rollout config enables OCR/artifacts and keeps risky capabilities disabled.');
