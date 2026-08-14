/**
 * Coverage for the two built artifacts nothing else tests: search.cjs and
 * packages/api's index.cjs.
 *
 * Adversarial review 2026-08-14, finding 3: the image-build workflow claimed to
 * "run patch behaviour tests against the built image" while loading only three
 * of five artifacts. The reviewer changed `topResults` from 3 to 999 in a built
 * search.cjs and all three suites stayed green. index.cjs was not even copied,
 * and stream_services.test.cjs asserts the old entrypoint transform against a
 * synthetic fixture — not the artifact the runtime loads.
 *
 * Runs against a directory of artifacts extracted from a built image
 * (KLAI_BUILT_ARTIFACTS_DIR). Under CI a missing directory is a FAILURE, not a
 * skip: a silently-skipped test is the same coverage lie this file exists to
 * correct.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const artifactsDir = process.env.KLAI_BUILT_ARTIFACTS_DIR;

if (!artifactsDir) {
  // Skipping here is legitimate: the generic LibreChat test workflow has no
  // built image to point at. The protection against this suite quietly falling
  // out of CI is not a check in this file -- it is
  // librechat-patched-files.test.mjs asserting that the image-build workflow
  // still runs it with KLAI_BUILT_ARTIFACTS_DIR set.
  console.log(
    'SKIPPED: built_artifacts.test.cjs needs KLAI_BUILT_ARTIFACTS_DIR ' +
      '(set by .github/workflows/librechat-image-build.yml). Nothing asserted.'
  );
  return;
}

// ---------------------------------------------------------------------------
// search.cjs — behavioural where the surface allows it
// ---------------------------------------------------------------------------
const searchSource = fs.readFileSync(
  path.join(artifactsDir, 'search.cjs'),
  'utf8'
);

const sandbox = {
  Buffer,
  console,
  process,
  URL,
  TextEncoder,
  TextDecoder,
  setTimeout,
  clearTimeout,
  module: { exports: {} },
  exports: {},
};
sandbox.require = (id) => {
  try {
    return require(id);
  } catch {
    // The bundle pulls in scraper/LLM deps this test never exercises.
    return new Proxy({}, { get: () => () => {} });
  }
};
sandbox.module.exports = sandbox.exports;
vm.createContext(sandbox);
new vm.Script(searchSource, { filename: 'search.cjs' }).runInContext(sandbox);

const searchExports = sandbox.module.exports;
assert.equal(typeof searchExports.createSourceProcessor, 'function');
assert.equal(typeof searchExports.createSearchAPI, 'function');

const stubScraper = {
  extractMetadata: () => ({}),
  extractContent: (response) => [response.data, []],
};

// The exact mutation the reviewer used to prove the old suite was blind.
assert.equal(
  searchExports.createSourceProcessor({ topResults: 3 }, stubScraper).topResults,
  3,
  'createSourceProcessor must honour the configured topResults'
);
// search.ts.patch lowers upstream's default of 5 to 3 -- one of the four
// semantic changes in that diff. Pinning it here catches both a silent revert
// to upstream and the arbitrary value the reviewer used to prove the old
// suite was blind.
assert.equal(
  searchExports.createSourceProcessor({}, stubScraper).topResults,
  3,
  'the Klai default of topResults=3 did not survive the build'
);

// Structural, and labelled as such. `chunker.cleanText` is module-internal and
// only reachable through processSources, which needs a full provider-shaped
// scrape response to drive. Asserting the guard survived the build is weaker
// than exercising it, but it does catch the failure mode that matters here: a
// source diff that silently stops reaching the built bundle.
const LONE_SURROGATE_STRIP =
  '[\\uD800-\\uDBFF](?![\\uDC00-\\uDFFF])|(?<![\\uD800-\\uDBFF])[\\uDC00-\\uDFFF]';
const surrogateGuards = searchSource.split(LONE_SURROGATE_STRIP).length - 1;
assert.equal(
  surrogateGuards,
  2,
  `expected the lone-surrogate strip in both cleanText and the chunk splitter, found ${surrogateGuards}`
);
assert.ok(
  searchSource.includes('[klai-patch] search.cleanText stripped lone UTF-16 surrogate(s)'),
  'the cleanText surrogate warning did not survive the build'
);

// ---------------------------------------------------------------------------
// index.cjs — the artifact the runtime actually loads
// ---------------------------------------------------------------------------
// This is the file whose bind-mounted .ts counterpart was inert for months:
// the runtime loads packages/api/dist/index.cjs, and mounting the source did
// nothing. The source-diff model is supposed to make that impossible, so the
// check is "did the patch reach the built bundle".
const apiSource = fs.readFileSync(path.join(artifactsDir, 'index.cjs'), 'utf8');

assert.ok(
  apiSource.includes('cleanupOnComplete: CLEANUP_ON_COMPLETE'),
  'createStreamServices.ts.patch did not reach packages/api/dist/index.cjs — ' +
    'the exact inert-patch failure this SPEC exists to eliminate'
);
assert.ok(
  !/cleanupOnComplete:\s*true/.test(apiSource),
  'a hardcoded cleanupOnComplete: true survives in the built bundle'
);
// Both call sites (the services object and the manager config) must be wired,
// or completed jobs are still reaped on one of the two paths.
const wiredSites = apiSource.split('cleanupOnComplete: CLEANUP_ON_COMPLETE').length - 1;
assert.ok(
  wiredSites >= 2,
  `expected cleanupOnComplete wired at both call sites, found ${wiredSites}`
);


// ---------------------------------------------------------------------------
// stream.cjs — the two marker edge cases the source diffs fix
// ---------------------------------------------------------------------------
// These assert the FIXED behaviour, so they live here rather than in
// stream_sources.test.cjs: that suite guards the .cjs still bind-mounted in
// production, which predates these fixes. The fixes reach users through the
// planned canary rollout (Phase 3), not as a side effect of a build change.
const { loadStreamBundle } = require('./_stream-sandbox.cjs');
const streamExports = loadStreamBundle(
  fs.readFileSync(path.join(artifactsDir, 'stream.cjs'), 'utf8')
);
const { createKlaiSourcesFramer, createContentAggregator } = streamExports;
assert.equal(typeof createKlaiSourcesFramer, 'function');
assert.equal(typeof createContentAggregator, 'function');

const encodeMarker = (value) =>
  Buffer.from(JSON.stringify(value), 'utf8')
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');

function splitThroughFramer(body, cut) {
  const framer = createKlaiSourcesFramer();
  const first = framer.push(body.slice(0, cut));
  const second = framer.push(body.slice(cut));
  return { text: first.text + second.text, sources: first.sources ?? second.sources };
}

// Finding 6a: the complete-marker regex accepts any whitespace; the partial
// detector used to hardcode exactly one space, so this leaked from cut=16.
{
  const sources = [{ label: '1', url: 'https://example.com/a' }];
  const body = `Antwoord. <!--  klai_sources=${encodeMarker(sources)}  --> En verder.`;
  for (let cut = 1; cut < body.length; cut++) {
    const out = splitThroughFramer(body, cut);
    assert.ok(
      !out.text.includes('klai_sources'),
      `whitespace-variant marker leaked at cut=${cut}: ${JSON.stringify(out.text)}`
    );
    assert.equal(
      JSON.stringify(out.sources),
      JSON.stringify(sources),
      `whitespace-variant marker lost sources at cut=${cut}`
    );
  }
}

// Finding 6b: a legitimately large marker split past the old 8192 cap leaked
// in full. The cap is a safety valve, not a normal limit.
{
  const many = Array.from({ length: 400 }, (_, i) => ({
    label: String(i + 1),
    url: `https://example.com/${'p'.repeat(40)}${i}`,
  }));
  const body = `Antwoord. <!-- klai_sources=${encodeMarker(many)} --> En verder.`;
  assert.ok(body.length > 8192, 'this case must exceed the old cap to be meaningful');
  const out = splitThroughFramer(body, 8300);
  assert.ok(!out.text.includes('klai_sources'), 'oversized-but-valid marker leaked');
  assert.equal(out.sources.length, 400, 'oversized-but-valid marker lost its sources');
}

// Finding 4: `sources: []` is not nullish, so `??` let it suppress a valid
// inline marker. Only a non-empty list may count as "the producer spoke".
{
  const sources = [{ label: '1', url: 'https://example.com/a' }];
  const agg = createContentAggregator();
  agg.aggregateContent({
    event: 'on_run_step',
    data: { id: 's1', index: 0, stepDetails: { type: 'message_creation' } },
  });
  agg.aggregateContent({
    event: 'on_message_delta',
    data: {
      id: 's1',
      delta: {
        content: [
          {
            type: 'text',
            text: `Antwoord. <!-- klai_sources=${encodeMarker(sources)} -->`,
            sources: [],
          },
        ],
      },
    },
  });
  assert.equal(agg.contentParts[0].text, 'Antwoord. ');
  assert.equal(
    JSON.stringify(agg.contentParts[0].sources),
    JSON.stringify(sources),
    'an empty sources array must not suppress the marker sources'
  );
}


// ---------------------------------------------------------------------------
// messages.js — SPEC-KB-015 feedback forwarding (Phase 4)
// ---------------------------------------------------------------------------
// Plain runtime JS, COPYed rather than bundled, so the source diff IS what
// runs. That also makes the marker survive, which is how the runtime transform
// in klai-entrypoint.sh knows to stand down instead of applying a second copy.
const messagesSource = fs.readFileSync(path.join(artifactsDir, 'messages.js'), 'utf8');

assert.ok(
  messagesSource.includes('SPEC-KB-015'),
  'messages.js.patch did not reach the built image — the runtime transform would ' +
    'then apply on top instead of standing down'
);
assert.ok(
  messagesSource.includes('/internal/v1/kb-feedback'),
  'the kb-feedback forward call is missing from the built messages.js'
);
// Fire-and-forget by contract (REQ-KB-015-06): the forward must never block or
// fail the user's response. Asserted structurally -- an earlier version measured
// the character distance between fetch( and .catch(, which broke the moment a
// comment was added inside the block. A test that a comment can fail is
// measuring the wrong thing.
const forwardStart = messagesSource.indexOf('/internal/v1/kb-feedback');
assert.ok(forwardStart !== -1, 'kb-feedback forward not found');
// The forward sits immediately before the response is sent, so everything it
// needs must be wired up in between.
const responseStart = messagesSource.indexOf('res.json(', forwardStart);
assert.ok(responseStart !== -1, 'no res.json after the kb-feedback forward');
const forwardBlock = messagesSource.slice(forwardStart, responseStart);
assert.ok(
  forwardBlock.includes('.catch('),
  'the kb-feedback forward must carry a .catch — a rejected promise would otherwise surface to the user'
);
assert.ok(
  !/await\s+fetch\(`\$\{portalUrl\}\/internal\/v1\/kb-feedback/.test(messagesSource),
  'the kb-feedback forward must not be awaited'
);
// The correlation identity has to travel with it, or the receiving end falls
// back to the LibreChat user id and correlation silently fails again.
assert.ok(
  forwardBlock.includes('identity_user_id'),
  'the forward must send identity_user_id (the Zitadel subject the retrieval log is keyed by)'
);

console.log(
  'OK: built artifacts verified — search topResults + surrogate guards, ' +
    'index.cjs cleanupOnComplete at every call site, stream marker edge cases, ' +
    'messages.js kb-feedback forward.'
);
