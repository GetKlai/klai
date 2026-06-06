const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');

const repoRoot = path.resolve(__dirname, '../../..');
const patcher = path.join(repoRoot, 'deploy/librechat/getklai/apply-canary-config.py');
const compose = fs.readFileSync(path.join(repoRoot, 'deploy/docker-compose.yml'), 'utf8');
const workflow = fs.readFileSync(path.join(repoRoot, '.github/workflows/deploy-compose.yml'), 'utf8');
const entrypoint = fs.readFileSync(path.join(repoRoot, 'deploy/librechat/getklai/entrypoint.sh'), 'utf8');
const globalEntrypoint = fs.readFileSync(path.join(repoRoot, 'deploy/librechat/klai-entrypoint.sh'), 'utf8');

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
assert.match(workflow, /cleanup_stale_librechat_getklai_container\(\)/);
assert.match(workflow, /docker compose --project-directory \/opt\/klai ps -aq "\$svc"/);
assert.match(workflow, /docker ps -aq --filter "name=\^\/\$\{svc\}\$"/);
assert.match(workflow, /docker rm -f "\$exact_name_id"/);
assert.match(workflow, /force-recreating librechat-getklai via compose-up wrapper/);
assert.match(workflow, /\/opt\/klai\/scripts\/compose-up\.sh --force-recreate librechat-getklai/);
assert.doesNotMatch(
  workflow,
  /docker compose --project-directory \/opt\/klai up -d --force-recreate librechat-getklai/,
);

class Element {
  constructor(tagName = 'DIV') {
    this.tagName = tagName;
    this.dataset = {};
    this.classList = { contains: () => false };
    this.nextElementSibling = null;
    this.style = {};
    this.textContent = '';
  }

  querySelectorAll() {
    return [];
  }

  querySelector() {
    return null;
  }

  closest() {
    return null;
  }

  replaceWith() {}
  appendChild() {}
  append() {}
}

function assertEntrypointIsNullSafe(fileName, source) {
  assert.match(source, /KB_DISCLOSURE_MARKER=klai-kb-disclosure-v8/, fileName);
  assert.doesNotMatch(source, /klai-kb-disclosure-v7/, fileName);
  assert.doesNotMatch(source, /root\.querySelectorAll\?\./, fileName);
  assert.match(source, /klai-hide-librechat-footer-v1/, fileName);
  assert.match(source, /\[role="contentinfo"\]\{display:none!important\}/, fileName);

  const match = source.match(
    /<script id="klai-kb-disclosure-script">\/\*klai-kb-disclosure-v8\*\/\n([\s\S]*?)<\/script>/,
  );
  assert.ok(match, `${fileName}: disclosure script not found`);

  assert.doesNotThrow(() => {
    vm.runInNewContext(match[1], {
      HTMLElement: Element,
      MutationObserver: class {
        observe() {}
      },
      document: {
        body: null,
        documentElement: new Element('HTML'),
        readyState: 'complete',
        createElement: (tagName) => new Element(tagName.toUpperCase()),
        addEventListener() {},
      },
      window: {
        queueMicrotask(fn) {
          fn();
        },
      },
    });
  }, `${fileName}: disclosure script must tolerate document.body === null`);
}

assertEntrypointIsNullSafe('deploy/librechat/getklai/entrypoint.sh', entrypoint);
assertEntrypointIsNullSafe('deploy/librechat/klai-entrypoint.sh', globalEntrypoint);

console.log('OK: LibreChat config disables risky v0.8.6 capabilities and entrypoint injection is null-safe.');
