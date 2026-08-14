#!/usr/bin/env node
/**
 * Pull the patched artifacts out of a built image so the existing behaviour
 * tests can run against what CI actually produced, rather than against the
 * hand-maintained .cjs snapshots in deploy/librechat/patches/.
 *
 * That distinction is the point: a source diff that applies cleanly and builds
 * successfully can still change behaviour. Only running the guards against the
 * built artifact catches that.
 *
 * usage: extract-librechat-patched-files.mjs --image <ref> --out <dir>
 */
import { PATCHED_FILES } from './lib/librechat-patched-files.mjs';
import { extractFromImage } from './lib/librechat-image-extract.mjs';

function arg(name) {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx === -1 || idx === process.argv.length - 1) {
    console.error(`FATAL: missing required --${name}`);
    process.exit(2);
  }
  return process.argv[idx + 1];
}

const image = arg('image');
const out = arg('out');

const { dir, files } = extractFromImage(image, PATCHED_FILES, out);
console.log(`Extracted ${files.size} patched artifacts from ${image} into ${dir}`);
for (const [key, hostPath] of files) console.log(`  ${key} -> ${hostPath}`);
