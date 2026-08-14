import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/**
 * Copy paths out of an image without running it.
 *
 * `docker create` + `docker cp` touches no entrypoint and needs no running
 * container, which matters here: the LibreChat entrypoint expects a full
 * tenant environment and would fail. It also keeps this usable against an
 * image that is deliberately not deployable yet (Phase 2 pushes a tag nothing
 * points at).
 *
 * Set KLAI_LIBRECHAT_EXTRACT_DIR to a directory of `<key>` files to bypass
 * Docker entirely. That is what lets the manifest generate/verify logic be
 * tested without a daemon and without a 10-minute image build — the same
 * injection trick assert-safe-to-prune.sh uses for its mount table.
 *
 * @param {string} image
 * @param {Array<{key: string, containerPath: string}>} entries
 * @param {string} [outDir] Defaults to a fresh temp dir.
 * @returns {{dir: string, files: Map<string, string>}} key -> host path
 */
export function extractFromImage(image, entries, outDir) {
  const injected = process.env.KLAI_LIBRECHAT_EXTRACT_DIR;
  if (injected) {
    const files = new Map();
    for (const entry of entries) {
      const hostPath = path.join(injected, entry.key);
      if (!fs.existsSync(hostPath)) {
        throw new Error(
          `KLAI_LIBRECHAT_EXTRACT_DIR is set but ${hostPath} is missing ` +
            `(needed for ${entry.containerPath})`
        );
      }
      files.set(entry.key, hostPath);
    }
    return { dir: injected, files };
  }

  const dir = outDir ?? fs.mkdtempSync(path.join(os.tmpdir(), 'klai-lc-'));
  fs.mkdirSync(dir, { recursive: true });

  const containerId = execFileSync('docker', ['create', image], {
    encoding: 'utf8',
  }).trim();

  const files = new Map();
  try {
    for (const entry of entries) {
      const dest = path.join(dir, entry.key);
      execFileSync('docker', [
        'cp',
        `${containerId}:${entry.containerPath}`,
        dest,
      ]);
      files.set(entry.key, dest);
    }
  } finally {
    execFileSync('docker', ['rm', '-f', containerId], { stdio: 'ignore' });
  }

  return { dir, files };
}

/** @param {string} filePath */
export function sha256File(filePath) {
  return createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

/** @param {string} value */
export function sha256String(value) {
  return createHash('sha256').update(value).digest('hex');
}
