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
 * The extractor is a parameter, not an env var. Tests pass a fake one; there is
 * no switch that can be left on in a deploy environment and turn this check
 * into a no-op.
 *
 * usage: verify-librechat-build-manifest.mjs --image <ref>
 */
import fs from 'node:fs';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

import {
  MANIFEST_CONTAINER_PATH,
  PATCHED_FILES,
} from './lib/librechat-patched-files.mjs';
import {
  extractFromImage,
  sha256File,
} from './lib/librechat-image-extract.mjs';

/**
 * @param {object} options
 * @param {string} options.image
 * @param {typeof extractFromImage} [options.extract]
 * @returns {string} success summary
 * @throws {Error} with every mismatch listed
 */
export function verifyManifest({ image, extract = extractFromImage }) {
  const entries = [
    ...PATCHED_FILES,
    { key: 'build-manifest.json', containerPath: MANIFEST_CONTAINER_PATH },
  ];

  const { files } = extract(image, entries);

  const manifest = JSON.parse(
    fs.readFileSync(files.get('build-manifest.json'), 'utf8')
  );

  if (manifest.placeholder) {
    throw new Error(
      'the image carries the placeholder manifest — pass 2 of the build did not run.'
    );
  }

  const byKey = new Map(manifest.artifacts.map((a) => [a.key, a]));
  const failures = [];

  for (const entry of PATCHED_FILES) {
    const recorded = byKey.get(entry.key);
    if (!recorded) {
      failures.push(
        `${entry.key}: patched by the Dockerfile but absent from the manifest`
      );
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
    throw new Error(
      `manifest does not describe ${image}:\n` +
        failures.map((f) => `  - ${f}`).join('\n')
    );
  }

  return (
    `OK: ${image} matches its baked manifest (${manifest.artifacts.length} artifacts, ` +
    `upstream ${manifest.upstream_librechat_tag}, agents ${manifest.agents_ref}).`
  );
}

const invokedDirectly =
  process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

if (invokedDirectly) {
  const idx = process.argv.indexOf('--image');
  if (idx === -1 || idx === process.argv.length - 1) {
    console.error('FATAL: missing required --image');
    process.exit(2);
  }
  try {
    console.log(verifyManifest({ image: process.argv[idx + 1] }));
  } catch (error) {
    console.error(`FATAL: ${error.message}`);
    process.exit(1);
  }
}
