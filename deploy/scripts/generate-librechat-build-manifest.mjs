#!/usr/bin/env node
/**
 * REQ-5 — build manifest with patched-file provenance.
 *
 * Records, for every patched artifact: the upstream tag it was built from, the
 * diff that produced it (and that diff's own hash), and the SHA256 of the
 * result inside the image. This is what makes the image self-describing:
 * `patch-manifest.txt` could only say "the upstream file we patched had hash
 * X", never "the file we shipped has hash Y". A stale or partially-applied
 * patch was invisible to it. Here it is not.
 *
 * usage:
 *   generate-librechat-build-manifest.mjs --image <ref> --upstream-tag <tag>
 *     --agents-ref <ref> --patch-revision <n> --patches-dir <dir> --out <file>
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

import { PATCHED_FILES } from './lib/librechat-patched-files.mjs';
import {
  extractFromImage,
  sha256File,
} from './lib/librechat-image-extract.mjs';

/**
 * @param {object} options
 * @param {string} options.image
 * @param {string} options.upstreamTag
 * @param {string} options.agentsRef
 * @param {string} options.patchRevision
 * @param {string} options.patchesDir
 * @param {typeof extractFromImage} [options.extract]
 * @returns {object} the manifest
 */
export function generateManifest({
  image,
  upstreamTag,
  agentsRef,
  patchRevision,
  patchesDir,
  extract = extractFromImage,
}) {
  const { files } = extract(image, PATCHED_FILES);

  const artifacts = PATCHED_FILES.map((entry) => {
    const patchPath = path.join(patchesDir, entry.patch);
    if (!fs.existsSync(patchPath)) {
      throw new Error(`diff not found: ${patchPath}`);
    }
    return {
      key: entry.key,
      lane: entry.lane,
      container_path: entry.containerPath,
      patch: entry.patch,
      patch_sha256: sha256File(patchPath),
      artifact_sha256: sha256File(files.get(entry.key)),
    };
  });

  return {
    spec: 'SPEC-LIBRECHAT-PATCH-MODEL-001',
    schema_version: 1,
    upstream_librechat_tag: upstreamTag,
    // Resolved from the upstream image, never pinned independently -- see the
    // workflow's resolve step and the 2026-08-13 version-skew incident.
    agents_ref: agentsRef,
    klai_patch_revision: patchRevision,
    artifacts,
  };
}

const invokedDirectly =
  process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

if (invokedDirectly) {
  const arg = (name) => {
    const idx = process.argv.indexOf(`--${name}`);
    if (idx === -1 || idx === process.argv.length - 1) {
      console.error(`FATAL: missing required --${name}`);
      process.exit(2);
    }
    return process.argv[idx + 1];
  };

  const out = arg('out');
  try {
    const manifest = generateManifest({
      image: arg('image'),
      upstreamTag: arg('upstream-tag'),
      agentsRef: arg('agents-ref'),
      patchRevision: arg('patch-revision'),
      patchesDir: arg('patches-dir'),
    });
    fs.writeFileSync(out, `${JSON.stringify(manifest, null, 2)}\n`);
    console.log(`Wrote ${out} (${manifest.artifacts.length} artifacts)`);
  } catch (error) {
    console.error(`FATAL: ${error.message}`);
    process.exit(1);
  }
}
