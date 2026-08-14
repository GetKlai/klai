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

import {
  PATCHED_FILES,
} from './lib/librechat-patched-files.mjs';
import {
  extractFromImage,
  sha256File,
} from './lib/librechat-image-extract.mjs';

function arg(name, required = true) {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx === -1 || idx === process.argv.length - 1) {
    if (!required) return undefined;
    console.error(`FATAL: missing required --${name}`);
    process.exit(2);
  }
  return process.argv[idx + 1];
}

const image = arg('image');
const upstreamTag = arg('upstream-tag');
const agentsRef = arg('agents-ref');
const patchRevision = arg('patch-revision');
const patchesDir = arg('patches-dir');
const out = arg('out');

const { files } = extractFromImage(image, PATCHED_FILES);

const artifacts = PATCHED_FILES.map((entry) => {
  const hostPath = files.get(entry.key);
  const patchPath = path.join(patchesDir, entry.patch);
  if (!fs.existsSync(patchPath)) {
    console.error(`FATAL: diff not found: ${patchPath}`);
    process.exit(1);
  }
  return {
    key: entry.key,
    lane: entry.lane,
    container_path: entry.containerPath,
    patch: entry.patch,
    patch_sha256: sha256File(patchPath),
    artifact_sha256: sha256File(hostPath),
  };
});

const manifest = {
  spec: 'SPEC-LIBRECHAT-PATCH-MODEL-001',
  schema_version: 1,
  upstream_librechat_tag: upstreamTag,
  // Resolved from the upstream image, never pinned independently -- see the
  // workflow's resolve step and the 2026-08-13 version-skew incident.
  agents_ref: agentsRef,
  klai_patch_revision: patchRevision,
  artifacts,
};

fs.writeFileSync(out, `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`Wrote ${out} (${artifacts.length} artifacts)`);
