#!/usr/bin/env node
/**
 * CI bundle size check.
 * Fails with exit code 1 if klai-chat.js (gzipped) exceeds 200 kB.
 */

import { readFileSync } from "fs";
import { gzipSync } from "zlib";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const bundlePath = resolve(__dirname, "../dist/klai-chat.js");

let bundle;
try {
  bundle = readFileSync(bundlePath);
} catch {
  console.error(`ERROR: Could not read bundle at ${bundlePath}`);
  console.error("Run `npm run build` first.");
  process.exit(1);
}

const compressed = gzipSync(bundle, { level: 9 });
const sizeBytes = compressed.length;
const sizeKb = (sizeBytes / 1024).toFixed(1);
const limitBytes = 200 * 1024; // 200 kB

if (sizeBytes > limitBytes) {
  console.error(
    `FAIL: Bundle size ${sizeKb} kB (gzipped) exceeds the 200 kB limit.`
  );
  console.error(`      Reduce dependencies or enable more aggressive tree-shaking.`);
  process.exit(1);
} else {
  console.log(
    `OK: Bundle size ${sizeKb} kB (gzipped) — within the 200 kB limit.`
  );
  process.exit(0);
}
