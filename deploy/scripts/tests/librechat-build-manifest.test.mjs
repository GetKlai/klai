/**
 * Tests for the build-manifest generate/verify pair (REQ-5 / REQ-6 seed).
 *
 * The fake image is injected as a function argument. It used to be an env var
 * (KLAI_LIBRECHAT_EXTRACT_DIR) read inside the extractor, which meant the same
 * switch that made these tests convenient could disable the deploy-time
 * provenance check in production. A guard whose whole job is to be trustworthy
 * must not ship its own off switch — see the "no environment escape hatch"
 * test at the bottom.
 *
 * Run: node --test deploy/scripts/tests/librechat-build-manifest.test.mjs
 */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { PATCHED_FILES } from '../lib/librechat-patched-files.mjs';
import { generateManifest } from '../generate-librechat-build-manifest.mjs';
import { verifyManifest } from '../verify-librechat-build-manifest.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '../../..');
const patchesDir = path.join(repoRoot, 'deploy/librechat/patches-source');

/** A directory of files standing in for the contents of a built image. */
function fakeImage() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'klai-fake-image-'));
  for (const entry of PATCHED_FILES) {
    fs.writeFileSync(path.join(dir, entry.key), `contents of ${entry.key}\n`);
  }
  return dir;
}

/** Stand-in extractor: reads the fake image dir instead of calling Docker. */
function extractorFor(dir) {
  return (_image, entries) => {
    const files = new Map();
    for (const entry of entries) {
      const hostPath = path.join(dir, entry.key);
      if (!fs.existsSync(hostPath)) {
        throw new Error(`fake image is missing ${entry.key}`);
      }
      files.set(entry.key, hostPath);
    }
    return { dir, files };
  };
}

function generateInto(dir) {
  const manifest = generateManifest({
    image: 'fake:tag',
    upstreamTag: 'v0.8.7',
    agentsRef: 'v3.2.46',
    patchRevision: '1',
    patchesDir,
    extract: extractorFor(dir),
  });
  fs.writeFileSync(
    path.join(dir, 'build-manifest.json'),
    `${JSON.stringify(manifest, null, 2)}\n`
  );
  return manifest;
}

test('manifest records every patched artifact with both hashes', () => {
  const dir = fakeImage();
  const manifest = generateInto(dir);

  assert.equal(manifest.artifacts.length, PATCHED_FILES.length);
  assert.equal(manifest.upstream_librechat_tag, 'v0.8.7');
  assert.equal(manifest.agents_ref, 'v3.2.46');
  for (const artifact of manifest.artifacts) {
    // Both the diff that produced it AND the result it produced.
    // patch-manifest.txt only ever had the upstream-original hash, which is
    // why a stale patch was invisible to it.
    assert.match(artifact.patch_sha256, /^[0-9a-f]{64}$/, artifact.key);
    assert.match(artifact.artifact_sha256, /^[0-9a-f]{64}$/, artifact.key);
  }
});

test('verification passes when the image matches its manifest', () => {
  const dir = fakeImage();
  generateInto(dir);
  const summary = verifyManifest({ image: 'fake:tag', extract: extractorFor(dir) });
  assert.match(summary, /^OK: /);
});

test('verification FAILS when an artifact changed after the manifest was written', () => {
  const dir = fakeImage();
  generateInto(dir);

  // Exactly the drift this check exists for: the image no longer contains what
  // CI recorded. One byte is enough.
  fs.appendFileSync(path.join(dir, 'stream.cjs'), '// tampered\n');

  assert.throws(
    () => verifyManifest({ image: 'fake:tag', extract: extractorFor(dir) }),
    /stream\.cjs: manifest says/
  );
});

test('verification FAILS on the placeholder manifest from build pass 1', () => {
  const dir = fakeImage();
  fs.writeFileSync(
    path.join(dir, 'build-manifest.json'),
    JSON.stringify({ placeholder: true })
  );

  assert.throws(
    () => verifyManifest({ image: 'fake:tag', extract: extractorFor(dir) }),
    /placeholder manifest/
  );
});

test('the CLI has no environment escape hatch that skips the image', () => {
  // Regression guard. An earlier revision honoured KLAI_LIBRECHAT_EXTRACT_DIR
  // inside extractFromImage, so setting it in a deploy environment made the
  // provenance check "pass" while inspecting local files instead of the image
  // about to roll out -- fail-open in the one place that must fail closed.
  const dir = fakeImage();
  generateInto(dir);

  assert.throws(
    () =>
      execFileSync(
        process.execPath,
        [
          path.join(here, '..', 'verify-librechat-build-manifest.mjs'),
          '--image',
          'this-image-does-not-exist:nope',
        ],
        {
          env: { ...process.env, KLAI_LIBRECHAT_EXTRACT_DIR: dir },
          stdio: 'pipe',
        }
      ),
    'verification succeeded against a non-existent image — an env var redirected it away from the image'
  );
});

// ---------------------------------------------------------------------------
// Adversarial review 2026-08-14, finding 2: without external anchors the check
// is self-attesting. It proved "this image agrees with its own manifest", not
// "this image is what CI built from our diffs". The reviewer forged upstream
// tag, agents ref, patch names and patch hashes and still got OK.
// ---------------------------------------------------------------------------

/** A manifest whose artifact hashes are honest but whose provenance is a lie. */
function forgedInto(dir) {
  const honest = generateManifest({
    image: 'fake:tag',
    upstreamTag: 'v0.8.7',
    agentsRef: 'v3.2.46',
    patchRevision: '1',
    patchesDir,
    extract: extractorFor(dir),
  });
  const forged = {
    ...honest,
    upstream_librechat_tag: 'v999-attacker',
    agents_ref: 'v999-attacker',
    klai_patch_revision: '666',
    artifacts: honest.artifacts.map((a) => ({
      ...a,
      patch: `wrong-${a.patch}`,
      patch_sha256: '0'.repeat(64),
    })),
  };
  fs.writeFileSync(
    path.join(dir, 'build-manifest.json'),
    `${JSON.stringify(forged, null, 2)}\n`
  );
}

test('forged provenance is caught when expectations are supplied', () => {
  const dir = fakeImage();
  forgedInto(dir);

  assert.throws(
    () =>
      verifyManifest({
        image: 'fake:tag',
        expectUpstreamTag: 'v0.8.7',
        expectAgentsRef: 'v3.2.46',
        expectPatchRevision: '1',
        patchesDir,
        extract: extractorFor(dir),
      }),
    (error) => {
      // Every lie must be named, not just the first one an operator trips over.
      assert.match(error.message, /upstream_librechat_tag: expected v0\.8\.7/);
      assert.match(error.message, /agents_ref: expected v3\.2\.46/);
      assert.match(error.message, /klai_patch_revision: expected 1/);
      assert.match(error.message, /built from wrong-/);
      return true;
    }
  );
});

test('a manifest built from different diffs than this checkout is rejected', () => {
  const dir = fakeImage();
  const manifest = generateManifest({
    image: 'fake:tag',
    upstreamTag: 'v0.8.7',
    agentsRef: 'v3.2.46',
    patchRevision: '1',
    patchesDir,
    extract: extractorFor(dir),
  });
  // Same diff names, different content: the image was built from an older or
  // tampered revision of our own patches.
  manifest.artifacts[0].patch_sha256 = 'f'.repeat(64);
  fs.writeFileSync(
    path.join(dir, 'build-manifest.json'),
    `${JSON.stringify(manifest, null, 2)}\n`
  );

  assert.throws(
    () =>
      verifyManifest({
        image: 'fake:tag',
        patchesDir,
        extract: extractorFor(dir),
      }),
    /this checkout hashes to [0-9a-f]{64}, the image was built from f{64}/
  );
});
