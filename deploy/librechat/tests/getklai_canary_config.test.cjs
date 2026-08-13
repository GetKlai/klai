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
assert.match(patched, /^version: 1\.3\.13$/m);
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
  assert.match(source, /KB_DISCLOSURE_MARKER=klai-kb-disclosure-v9/, fileName);
  assert.doesNotMatch(source, /klai-kb-disclosure-v[0-8](?![0-9])/, fileName);
  assert.doesNotMatch(source, /root\.querySelectorAll\?\./, fileName);
  assert.match(source, /klai-hide-librechat-footer-v1/, fileName);
  assert.match(source, /\[role="contentinfo"\]\{display:none!important\}/, fileName);

  const match = source.match(
    /<script id="klai-kb-disclosure-script">\/\*klai-kb-disclosure-v9\*\/\n([\s\S]*?)<\/script>/,
  );
  assert.ok(match, `${fileName}: disclosure script not found`);
  // SPEC-CHAT-SOURCE-DISCLOSURE-001 Fase 3: v9 is language-neutral.
  assert.match(match[1], /"sources"/, `${fileName}: v9 must recognise the English "Sources" heading`);
  assert.match(match[1], /agent activity/i, `${fileName}: v9 must recognise the English "Agent activity" heading`);
  assert.match(match[1], /navigator\.language/, `${fileName}: v9 labels must key on navigator.language`);
  assert.doesNotMatch(
    match[1],
    /new Set\(\["Bronnen","Agent activiteit"\]\)/,
    `${fileName}: v9 must drop the NL-only heading set`,
  );

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

// The disclosure style+script block is duplicated in both entrypoints; any
// edit to one and not the other drifts silently. Enforce byte-identity.
function extractDisclosureBlock(fileName, source) {
  const match = source.match(
    /<style id="klai-kb-disclosure-style">[\s\S]*?<\/script>/,
  );
  assert.ok(match, `${fileName}: disclosure block not found`);
  return match[0];
}
assert.equal(
  extractDisclosureBlock('deploy/librechat/getklai/entrypoint.sh', entrypoint),
  extractDisclosureBlock('deploy/librechat/klai-entrypoint.sh', globalEntrypoint),
  'disclosure blocks drifted between getklai/entrypoint.sh and klai-entrypoint.sh',
);

// SPEC-CHAT-SOURCE-DISCLOSURE-001 Fase 3: the v9 disclosure script must render
// panels from a footer in BOTH nl and en, with count labels keyed on
// navigator.language. Runs the injected script against a minimal fake DOM
// containing a Sources/Bronnen + Agent activity/Agent activiteit footer.
function runDisclosure(source, { language, sourcesHeading, activityHeading }) {
  const scriptMatch = source.match(
    /<script id="klai-kb-disclosure-script">\/\*klai-kb-disclosure-v9\*\/\n([\s\S]*?)<\/script>/,
  );
  assert.ok(scriptMatch, 'v9 disclosure script not found');
  const created = [];
  class El {
    constructor(tag) {
      this.tagName = (tag || 'DIV').toUpperCase();
      this.dataset = {};
      this.style = {};
      this.className = '';
      this.textContent = '';
      this.nextElementSibling = null;
      this._q = [];
      this._children = [];
      const owned = new Set();
      this.classList = { contains: (c) => owned.has(c), add: (c) => owned.add(c) };
    }
    querySelectorAll() {
      return this._q;
    }
    querySelector() {
      return null;
    }
    closest() {
      return null;
    }
    append(...nodes) {
      this._children.push(...nodes);
    }
    appendChild(node) {
      this._children.push(node);
      return node;
    }
    replaceWith(node) {
      this.replacedWith = node;
    }
  }
  const mk = (tag, text) => {
    const e = new El(tag);
    if (text != null) e.textContent = text;
    return e;
  };
  const sHead = mk('STRONG', sourcesHeading);
  const sList = mk('UL');
  sList._q = [mk('LI', 'a'), mk('LI', 'b')];
  const aHead = mk('STRONG', activityHeading);
  const aList = mk('UL');
  aList._q = [mk('LI', 'x')];
  sHead.nextElementSibling = sList;
  sList.nextElementSibling = aHead;
  aHead.nextElementSibling = aList;
  aList.nextElementSibling = null;
  const body = new El('DIV');
  body._q = [sHead, ...sList._q, aHead, ...aList._q];
  vm.runInNewContext(scriptMatch[1], {
    HTMLElement: El,
    MutationObserver: class {
      observe() {}
    },
    navigator: { language },
    document: {
      body,
      documentElement: new El('HTML'),
      readyState: 'complete',
      createElement: (tag) => {
        const e = new El(tag.toUpperCase());
        created.push(e);
        return e;
      },
      addEventListener() {},
    },
    window: {
      queueMicrotask(fn) {
        fn();
      },
    },
  });
  return { created, sHead, aHead };
}

function assertBilingualPanels(fileName, source, expected) {
  const { created, sHead, aHead } = runDisclosure(source, expected);
  const details = created.filter((e) => e.tagName === 'DETAILS');
  assert.ok(
    details.find((e) => e.className.includes('klai-kb-disclosure--sources')),
    `${fileName} [${expected.language}]: sources panel for "${expected.sourcesHeading}"`,
  );
  assert.ok(
    details.find((e) => e.className.includes('klai-kb-disclosure--activity')),
    `${fileName} [${expected.language}]: activity panel for "${expected.activityHeading}"`,
  );
  assert.ok(sHead.replacedWith, `${fileName} [${expected.language}]: sources heading replaced`);
  assert.ok(aHead.replacedWith, `${fileName} [${expected.language}]: activity heading replaced`);
  const counts = created
    .filter((e) => e.className === 'klai-kb-disclosure-count')
    .map((e) => e.textContent);
  assert.ok(
    counts.includes(expected.sourcesLabel),
    `${fileName} [${expected.language}]: expected "${expected.sourcesLabel}", got ${JSON.stringify(counts)}`,
  );
  assert.ok(
    counts.includes(expected.activityLabel),
    `${fileName} [${expected.language}]: expected "${expected.activityLabel}", got ${JSON.stringify(counts)}`,
  );
}

for (const [fileName, source] of [
  ['deploy/librechat/getklai/entrypoint.sh', entrypoint],
  ['deploy/librechat/klai-entrypoint.sh', globalEntrypoint],
]) {
  assertBilingualPanels(fileName, source, {
    language: 'en-US',
    sourcesHeading: 'Sources',
    activityHeading: 'Agent activity',
    sourcesLabel: '2 sources',
    activityLabel: '1 step',
  });
  assertBilingualPanels(fileName, source, {
    language: 'nl-NL',
    sourcesHeading: 'Bronnen',
    activityHeading: 'Agent activiteit',
    sourcesLabel: '2 bronnen',
    activityLabel: '1 stap',
  });
}

console.log('OK: LibreChat config disables risky v0.8.6 capabilities and entrypoint injection is null-safe.');
