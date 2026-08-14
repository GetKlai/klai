/**
 * Single owner of "which files does the Klai LibreChat image patch, and where
 * do they live inside it".
 *
 * This list appears in two other places by necessity: the COPY lines in
 * deploy/librechat/Dockerfile.klai, and the diffs in
 * deploy/librechat/patches-source/. A contract spread across files drifts
 * silently (see `url-shape-multi-file-drift` in the klai pitfalls). So this
 * module is the source of truth and
 * deploy/scripts/tests/librechat-patched-files.test.mjs asserts the Dockerfile
 * agrees with it — a COPY added here but not there, or vice versa, fails CI.
 */

/**
 * @typedef {object} PatchedFile
 * @property {string} key          Stable short name, also the extraction filename.
 * @property {string} containerPath Absolute path inside the built image.
 * @property {string} patch        Diff under deploy/librechat/patches-source/.
 * @property {'agents'|'librechat'} lane Which build lane produces it.
 */

/** @type {PatchedFile[]} */
export const PATCHED_FILES = [
  {
    key: 'format.cjs',
    containerPath:
      '/app/node_modules/@librechat/agents/dist/cjs/messages/format.cjs',
    patch: 'format.ts.patch',
    lane: 'agents',
  },
  {
    key: 'stream.cjs',
    containerPath: '/app/node_modules/@librechat/agents/dist/cjs/stream.cjs',
    patch: 'stream.ts.patch',
    lane: 'agents',
  },
  {
    key: 'search.cjs',
    containerPath:
      '/app/node_modules/@librechat/agents/dist/cjs/tools/search/search.cjs',
    patch: 'search.ts.patch',
    lane: 'agents',
  },
  {
    key: 'share.js',
    containerPath: '/app/api/server/routes/share.js',
    patch: 'share.js.patch',
    lane: 'librechat',
  },
  {
    key: 'index.cjs',
    containerPath: '/app/packages/api/dist/index.cjs',
    patch: 'createStreamServices.ts.patch',
    lane: 'librechat',
  },
];

/** Path of the manifest baked into the image by the Dockerfile. */
export const MANIFEST_CONTAINER_PATH = '/klai-build-manifest.json';
