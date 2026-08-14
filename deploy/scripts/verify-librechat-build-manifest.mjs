#!/usr/bin/env node
/**
 * REQ-6 (seed) — deployed-artifact provenance verification.
 *
 * Reads the manifest baked into an image and re-hashes every patched file in
 * that same image. A mismatch means the manifest describes a different build
 * than the one you are holding — exactly the "second thing that can silently
 * drift" risk the SPEC calls out for this new image.
 *
 * Phase 2 runs this against the freshly built tag before it is pushed. Phase 3
 * extends the same check to deploy time, against the tag about to roll out.
 *
 * usage: verify-librechat-build-manifest.mjs --image <ref>
 */
import fs from 'node:fs';

import {
  MANIFEST_CONTAINER_PATH,
  PATCHED_FILES,
} from './lib/librechat-patched-files.mjs';
import {
  extractFromImage,
  sha256File,
} from './lib/librechat-image-extract.mjs';

const idx = process.argv.indexOf('--image');
if (idx === -1 || idx === process.argv.length - 1) {
  console.error('FATAL: missing required --image');
  process.exit(2);
}
const image = process.argv[idx + 1];

const entries = [
  ...PATCHED_FILES,
  { key: 'build-manifest.json', containerPath: MANIFEST_CONTAINER_PATH },
];

const { files } = extractFromImage(image, entries);

const manifestPath = files.get('build-manifest.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));

if (manifest.placeholder) {
  console.error(
    'FATAL: the image carries the placeholder manifest — pass 2 of the build did not run.'
  );
  process.exit(1);
}

const byKey = new Map(manifest.artifacts.map((a) => [a.key, a]));
const failures = [];

for (const entry of PATCHED_FILES) {
  const recorded = byKey.get(entry.key);
  if (!recorded) {
    failures.push(`${entry.key}: patched by the Dockerfile but absent from the manifest`);
    continue;
  }
  const actual = sha256File(files.get(entry.key));
  if (actual !== recorded.artifact_sha256) {
    failures.push(
      `${entry.key}: manifest says ${recorded.artifact_sha256}, image has ${actual}`
    );
  }
}

for (const recorded of manifest.artifacts) {
  if (!PATCHED_FILES.some((e) => e.key === recorded.key)) {
    failures.push(`${recorded.key}: in the manifest but not in PATCHED_FILES`);
  }
}

if (failures.length > 0) {
  console.error(`FATAL: manifest does not describe ${image}:`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}

console.log(
  `OK: ${image} matches its baked manifest (${manifest.artifacts.length} artifacts, ` +
    `upstream ${manifest.upstream_librechat_tag}, agents ${manifest.agents_ref}).`
);
