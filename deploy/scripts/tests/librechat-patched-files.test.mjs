/**
 * Drift test: the patched-file contract lives in three places by necessity --
 * PATCHED_FILES, the Dockerfile's COPY lines, and the diffs on disk. Two of
 * those are hand-edited. This test makes them agree or fails CI.
 *
 * Directly applies `url-shape-multi-file-drift` from the klai pitfalls: a
 * contract spread across files WILL drift, and the drift is invisible until a
 * real consumer round-trips it. Here the "real consumer" would be a production
 * rollout that quietly ships one unpatched file.
 *
 * Run: node --test deploy/scripts/tests/librechat-patched-files.test.mjs
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { PATCHED_FILES } from '../lib/librechat-patched-files.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const librechatDir = path.resolve(here, '../../librechat');
const dockerfile = fs.readFileSync(
  path.join(librechatDir, 'Dockerfile.klai'),
  'utf8'
);

/** Destination paths of the COPY --from=<stage> lines in the assembly stage. */
function dockerfileCopyTargets() {
  const targets = [];
  // Join continuation lines first: the COPY source and destination are split
  // across lines for readability.
  const joined = dockerfile.replace(/\\\n\s*/g, ' ');
  for (const line of joined.split('\n')) {
    const match = line.match(/^COPY\s+--from=\S+\s+(\S+)\s+(\S+)\s*$/);
    if (match) targets.push(match[2]);
  }
  return targets;
}

test('every PATCHED_FILES entry is COPYed by the Dockerfile', () => {
  const targets = dockerfileCopyTargets();
  for (const entry of PATCHED_FILES) {
    assert.ok(
      targets.includes(entry.containerPath),
      `${entry.key}: PATCHED_FILES says it lands at ${entry.containerPath}, ` +
        'but no COPY line in Dockerfile.klai writes there'
    );
  }
});

test('every Dockerfile COPY target is declared in PATCHED_FILES', () => {
  const declared = new Set(PATCHED_FILES.map((e) => e.containerPath));
  for (const target of dockerfileCopyTargets()) {
    assert.ok(
      declared.has(target),
      `Dockerfile.klai patches ${target}, but PATCHED_FILES does not declare ` +
        'it — the manifest and the provenance check would silently skip it'
    );
  }
});

test('every referenced diff exists on disk', () => {
  for (const entry of PATCHED_FILES) {
    const patchPath = path.join(librechatDir, 'patches-source', entry.patch);
    assert.ok(
      fs.existsSync(patchPath),
      `${entry.key}: references ${entry.patch}, which does not exist`
    );
  }
});

test('no diff on disk is orphaned', () => {
  const referenced = new Set(PATCHED_FILES.map((e) => e.patch));
  const onDisk = fs
    .readdirSync(path.join(librechatDir, 'patches-source'))
    .filter((f) => f.endsWith('.patch'));
  for (const file of onDisk) {
    assert.ok(
      referenced.has(file),
      `${file} sits in patches-source/ but no PATCHED_FILES entry applies it — ` +
        'it would never reach the image (the inert-patch failure mode again)'
    );
  }
});
