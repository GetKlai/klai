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
import path from 'node:path';
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
 * @param {string} [options.expectUpstreamTag]  Expected upstream LibreChat tag.
 * @param {string} [options.expectAgentsRef]    Expected @librechat/agents ref.
 * @param {string} [options.expectPatchRevision] Expected Klai patch revision.
 * @param {string} [options.patchesDir] Re-hash the diffs in THIS checkout and
 *   require the manifest to have been built from exactly them.
 * @param {typeof extractFromImage} [options.extract]
 * @returns {string} success summary
 * @throws {Error} with every mismatch listed
 */
export function verifyManifest({
  image,
  expectUpstreamTag,
  expectAgentsRef,
  expectPatchRevision,
  patchesDir,
  extract = extractFromImage,
}) {
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

  /* Without the checks below this function is self-attesting: it only proves
     the image is internally consistent with its own manifest, so an image
     built from entirely different diffs and a different upstream passes as
     long as it agrees with itself (adversarial review 2026-08-14, finding 2 --
     a manifest claiming upstream "v999-attacker" with zeroed patch hashes
     verified OK). Provenance has to be anchored to something OUTSIDE the
     artifact being checked: the caller's expectations and this checkout. */
  const expectations = [
    ['upstream_librechat_tag', expectUpstreamTag],
    ['agents_ref', expectAgentsRef],
    ['klai_patch_revision', expectPatchRevision],
  ];
  for (const [field, expected] of expectations) {
    if (expected === undefined) continue;
    if (manifest[field] !== expected) {
      failures.push(
        `${field}: expected ${expected}, manifest claims ${manifest[field]}`
      );
    }
  }

  if (patchesDir) {
    for (const entry of PATCHED_FILES) {
      const recorded = byKey.get(entry.key);
      if (!recorded) continue; // reported below
      if (recorded.patch !== entry.patch) {
        failures.push(
          `${entry.key}: built from ${recorded.patch}, this checkout applies ${entry.patch}`
        );
        continue;
      }
      const patchPath = path.join(patchesDir, entry.patch);
      if (!fs.existsSync(patchPath)) {
        failures.push(`${entry.patch}: not found in ${patchesDir}`);
        continue;
      }
      const actual = sha256File(patchPath);
      if (actual !== recorded.patch_sha256) {
        failures.push(
          `${entry.patch}: this checkout hashes to ${actual}, the image was built from ${recorded.patch_sha256}`
        );
      }
    }
  }

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
  const optional = (name) => {
    const idx = process.argv.indexOf(`--${name}`);
    return idx === -1 || idx === process.argv.length - 1
      ? undefined
      : process.argv[idx + 1];
  };

  const image = optional('image');
  if (!image) {
    console.error('FATAL: missing required --image');
    process.exit(2);
  }
  try {
    console.log(
      verifyManifest({
        image,
        expectUpstreamTag: optional('expect-upstream-tag'),
        expectAgentsRef: optional('expect-agents-ref'),
        expectPatchRevision: optional('expect-patch-revision'),
        patchesDir: optional('patches-dir'),
      })
    );
  } catch (error) {
    console.error(`FATAL: ${error.message}`);
    process.exit(1);
  }
}
