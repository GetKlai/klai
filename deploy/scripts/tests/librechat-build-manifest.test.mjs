/**
 * Tests for the build-manifest generate/verify pair (REQ-5 / REQ-6 seed),
 * without a Docker daemon: KLAI_LIBRECHAT_EXTRACT_DIR stands in for the image.
 *
 * The point of the manifest is to catch an image whose contents do not match
 * what CI says it built. So the test that matters is the negative one: change
 * a byte in an "image" file and verification must fail.
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

const here = path.dirname(fileURLToPath(import.meta.url));
const scriptsDir = path.resolve(here, '..');
const librechatDir = path.resolve(here, '../../librechat');

function fakeImage() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'klai-fake-image-'));
  for (const entry of PATCHED_FILES) {
    fs.writeFileSync(path.join(dir, entry.key), `contents of ${entry.key}\n`);
  }
  return dir;
}

function generate(imageDir, outPath) {
  execFileSync(
    process.execPath,
    [
      path.join(scriptsDir, 'generate-librechat-build-manifest.mjs'),
      '--image', 'fake:tag',
      '--upstream-tag', 'v0.8.7',
      '--agents-ref', 'v3.2.46',
      '--patch-revision', '1',
      '--patches-dir', path.join(librechatDir, 'patches-source'),
      '--out', outPath,
    ],
    { env: { ...process.env, KLAI_LIBRECHAT_EXTRACT_DIR: imageDir }, stdio: 'pipe' }
  );
}

function verify(imageDir) {
  return execFileSync(
    process.execPath,
    [path.join(scriptsDir, 'verify-librechat-build-manifest.mjs'), '--image', 'fake:tag'],
    { env: { ...process.env, KLAI_LIBRECHAT_EXTRACT_DIR: imageDir }, stdio: 'pipe', encoding: 'utf8' }
  );
}

test('manifest records every patched artifact with both hashes', () => {
  const imageDir = fakeImage();
  const out = path.join(imageDir, 'manifest.json');
  generate(imageDir, out);

  const manifest = JSON.parse(fs.readFileSync(out, 'utf8'));
  assert.equal(manifest.artifacts.length, PATCHED_FILES.length);
  assert.equal(manifest.upstream_librechat_tag, 'v0.8.7');
  assert.equal(manifest.agents_ref, 'v3.2.46');
  for (const artifact of manifest.artifacts) {
    // Both the diff that produced it AND the result it produced. patch-manifest.txt
    // only ever had the upstream-original hash, which is why a stale patch was
    // invisible to it.
    assert.match(artifact.patch_sha256, /^[0-9a-f]{64}$/, artifact.key);
    assert.match(artifact.artifact_sha256, /^[0-9a-f]{64}$/, artifact.key);
  }
});

test('verification passes when the image matches its manifest', () => {
  const imageDir = fakeImage();
  generate(imageDir, path.join(imageDir, 'build-manifest.json'));
  const output = verify(imageDir);
  assert.match(output, /^OK: /m);
});

test('verification FAILS when an artifact changed after the manifest was written', () => {
  const imageDir = fakeImage();
  generate(imageDir, path.join(imageDir, 'build-manifest.json'));

  // Exactly the drift this check exists for: the image no longer contains what
  // CI recorded. One byte is enough.
  fs.appendFileSync(path.join(imageDir, 'stream.cjs'), '// tampered\n');

  assert.throws(
    () => verify(imageDir),
    (error) => {
      const output = `${error.stdout ?? ''}${error.stderr ?? ''}`;
      assert.match(output, /stream\.cjs/);
      assert.match(output, /manifest says/);
      return true;
    }
  );
});

test('verification FAILS on the placeholder manifest from build pass 1', () => {
  const imageDir = fakeImage();
  fs.writeFileSync(
    path.join(imageDir, 'build-manifest.json'),
    JSON.stringify({ placeholder: true })
  );

  assert.throws(
    () => verify(imageDir),
    (error) => {
      const output = `${error.stdout ?? ''}${error.stderr ?? ''}`;
      assert.match(output, /placeholder manifest/);
      return true;
    }
  );
});
