const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '../../..');
const shareRoute = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/patches/share.js'),
  'utf8',
);
const sanitizerStart = shareRoute.indexOf('const PLACEHOLDER_HOSTS');
const sanitizerEnd = shareRoute.indexOf('/**\n * Shared messages');

assert.notEqual(sanitizerStart, -1, 'share sanitizer block start not found');
assert.notEqual(sanitizerEnd, -1, 'share sanitizer block end not found');

const sandbox = {
  URL,
  process: {
    env: {
      DOMAIN_CLIENT: 'https://chat-voys.getklai.com',
    },
  },
};

vm.runInNewContext(
  `${shareRoute.slice(sanitizerStart, sanitizerEnd)}\nthis.sanitizeMarkdown = sanitizeMarkdown;`,
  sandbox,
);

const { sanitizeMarkdown } = sandbox;

assert.equal(
  sanitizeMarkdown('Bekijk [Rekenvoorbeeld2](https://example.com/rekenvoorbeeld2)'),
  'Bekijk Rekenvoorbeeld2',
);

assert.equal(
  sanitizeMarkdown('![image](https://example.com/molair-volume-gas.png)\n\n📎 Rekenvoorbeeld2'),
  '\n\n📎 Rekenvoorbeeld2',
);

assert.equal(
  sanitizeMarkdown('Bron https://example.com/fake en https://example.org/fake'),
  'Bron  en ',
);

assert.equal(
  sanitizeMarkdown('![diagram](https://chat-voys.getklai.com/kb-images/diagram.png)'),
  '![diagram](https://chat-voys.getklai.com/kb-images/diagram.png)',
);

assert.equal(
  sanitizeMarkdown('[Docs](https://getklai.com/docs)'),
  '[Docs](https://getklai.com/docs)',
);

console.log('OK: LibreChat share sanitizer policy holds.');
