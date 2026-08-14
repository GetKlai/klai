const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '../../..');
const config = fs.readFileSync(path.join(repoRoot, 'deploy/librechat/librechat.yaml'), 'utf8');
const entrypoint = fs.readFileSync(path.join(repoRoot, 'deploy/librechat/klai-entrypoint.sh'), 'utf8');
const drift = fs.readFileSync(path.join(repoRoot, 'deploy/librechat/check-patch-drift.sh'), 'utf8');
const portalConfig = fs.readFileSync(path.join(repoRoot, 'klai-portal/backend/app/core/config.py'), 'utf8');
const workflow = fs.readFileSync(path.join(repoRoot, '.github/workflows/deploy-librechat-config.yml'), 'utf8');

assert.match(config, /^version: 1\.3\.13$/m);
// 2026-08-13: endpointsMenu/sidePanel are confirmed stripped from 0.8.7's
// interfaceSchema (zero references in packages/data-provider/src/config.ts
// inside ghcr.io/danny-avila/librechat:v0.8.7) -- setting them is a stale
// no-op, not a guard. sharedLinks is intentionally NOT pinned false: Klai
// already relies on shared links (ALLOW_SHARED_LINKS(_PUBLIC) forced true
// in provisioning + a dedicated share.js sanitizer patch), and upstream's
// own default for the new key matches that existing intent.
assert.doesNotMatch(config, /^\s+endpointsMenu:/m);
assert.doesNotMatch(config, /^\s+sidePanel:/m);
assert.match(config, /sharedLinks:\n(?:    .+\n)*    snapshotFiles: false/);
for (const key of ['autoSubmitFromUrl', 'buildInfo', 'contextUsage', 'contextCost', 'retainAgentFiles']) {
  assert.match(config, new RegExp(`^\\s+${key}: false$`, 'm'), `expected ${key}: false in interface block`);
}
assert.match(config, /skills:\n\s+use: false\n\s+create: false\n\s+share: false\n\s+defaultActiveOnShare: false/);
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
assert.match(workflow, /recreate_containers:/);
assert.match(workflow, /RECREATE_CONTAINERS=/);
assert.doesNotMatch(workflow, /regenerate\?recreate_containers=true/);
assert.match(workflow, /timeout=600\.0/);
// The drift guard used to hardcode the fleet image as upstream. It now
// resolves it the same way portal-api does -- compose LIBRECHAT_IMAGE first,
// config.py default as fallback -- because Phase 5 moved the fleet and a
// hardcoded guard would have validated an image nobody runs while reporting
// green. Pin the resolution ORDER rather than a specific image, so a future
// rollout does not have to edit this test to stay honest.
assert.match(drift, /COMPOSE_FLEET_IMAGE=/);
assert.match(drift, /CONFIG_DEFAULT_IMAGE=/);
assert.match(
  drift,
  /LIBRECHAT_IMAGE="\$\{LIBRECHAT_IMAGE:-\$\{COMPOSE_FLEET_IMAGE:-\$CONFIG_DEFAULT_IMAGE\}\}"/,
);
// config.py stays on upstream on purpose: it is the fallback when the compose
// variable is absent, so it must remain a working image rather than tracking
// whatever the fleet currently runs.
assert.match(portalConfig, /librechat_image: str = "ghcr\.io\/danny-avila\/librechat:v0\.8\.7"/);

// Finding 3D (adversarial review 2026-08-13): the deploy step must not treat
// a bare HTTP 200 as success. It must parse the JSON body and fail the job
// if the errors array is non-empty, even on a 200 -- belt and braces on top
// of the non-200 status check. Pin both halves of that contract so a future
// edit can't silently drop the errors-array check and go back to trusting
// the status code alone.
assert.match(workflow, /r\.status_code != 200/);
assert.match(workflow, /errors = body\.get\("errors"\) or \[\]/);
assert.match(workflow, /if errors:\s*\n\s*# Belt and braces/);
assert.match(workflow, /sys\.exit\(1\)/);
// The old bare bash-side "$HTTP_CODE" = "200" check must be gone -- that was
// exactly the class of check that let a 200-with-errors response go green.
assert.doesNotMatch(workflow, /HTTP_CODE.*=.*"200"/);

console.log('OK: global LibreChat rollout config enables OCR/artifacts and keeps risky capabilities disabled.');
console.log('OK: LibreChat regenerate deploy step fails loud on non-200 and on a non-empty errors array.');
