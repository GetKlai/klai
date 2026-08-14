#!/usr/bin/env node
'use strict';

/**
 * check-patch-drift.sh dry-run stage (2026-08-13 review finding 4).
 *
 * validate_runtime_targets() in check-patch-drift.sh only checks that the
 * runtime patch TARGET FILES still exist in $LIBRECHAT_IMAGE. It never
 * executes the transform, so an upstream LibreChat release that keeps the
 * files but changes their internal syntax (renamed variable, reshaped
 * function, moved anchor) passes CI green and the fleet crashloops at boot
 * (Meili patch throws) or silently loses behaviour (feedback patch used to
 * log-and-skip).
 *
 * This script closes that gap by EXECUTING the same Node.js transform logic
 * the entrypoints embed -- not a second copy of it. It extracts the heredoc
 * bodies directly out of klai-entrypoint.sh / getklai/entrypoint.sh (the
 * same technique deploy/librechat/tests/meili_tenant_indexes.test.cjs
 * already uses for synthetic fixtures) and runs them, unmodified except for
 * an absolute-path remap, against real files extracted from the pinned
 * LibreChat image. There is exactly one copy of the transform logic (the
 * entrypoint files); this script never duplicates it, so there is nothing
 * for a shared-helper copy to drift out of sync with.
 *
 * As a second, independent safety net, it also asserts the two entrypoint
 * files embed byte-identical transform blocks (the sync-guard fallback this
 * finding explicitly allows) -- so a hand-edit to only one of the two files
 * is caught even before the extracted-image execution runs.
 *
 * Usage:
 *   node dry-run-transforms.cjs <extracted-image-root> <klai-entrypoint.sh> <getklai-entrypoint.sh>
 *
 * Exit 0 on success. Exit 1 and print one "DRY-RUN FAIL [<transform>]: <reason>"
 * line per failure -- never a bare stack trace -- so CI output states plainly
 * which transform failed and why.
 */

const { existsSync, readFileSync } = require('fs');
const { spawnSync } = require('child_process');
const path = require('path');

const [, , extractedRoot, klaiEntrypointPath, getklaiEntrypointPath] = process.argv;

if (!extractedRoot || !klaiEntrypointPath || !getklaiEntrypointPath) {
  console.error(
    'usage: dry-run-transforms.cjs <extracted-image-root> <klai-entrypoint.sh> <getklai-entrypoint.sh>',
  );
  process.exit(2);
}

let failed = false;
function fail(transform, reason) {
  failed = true;
  console.error(`DRY-RUN FAIL [${transform}]: ${reason}`);
}
function ok(transform, msg) {
  console.log(`DRY-RUN OK [${transform}]: ${msg}`);
}

const klaiSrc = readFileSync(klaiEntrypointPath, 'utf8');
const getklaiSrc = readFileSync(getklaiEntrypointPath, 'utf8');

// Meili block: `node <<'NODE'` with no arguments before the heredoc marker
// (distinguishes it from the client-polish injection block later in the
// same files, which passes args before its own `<<'NODE'`).
const MEILI_RE = /node <<'NODE'\n([\s\S]*?)\nNODE\nfi/;
// Feedback-forward block: distinct heredoc marker, added 2026-08-13.
const FEEDBACK_RE = /node <<'KB_FEEDBACK_NODE'\n([\s\S]*?)\nKB_FEEDBACK_NODE\nfi/;
// Stream-cleanup block: distinct heredoc marker, added 2026-08-14. Patches
// the BUILT packages/api bundle (dist/index.cjs) in place -- the mounted
// createStreamServices.ts source patch it replaces was measured inert (the
// runtime loads the pre-built bundle via the @librechat/api workspace
// symlink, never the mounted source file).
const STREAM_CLEANUP_RE = /node <<'STREAM_CLEANUP_NODE'\n([\s\S]*?)\nSTREAM_CLEANUP_NODE\nfi/;

function extractBlock(src, re, label, fileLabel) {
  const m = src.match(re);
  if (!m) {
    fail(label, `could not locate the transform heredoc in ${fileLabel}`);
    return null;
  }
  return m[1];
}

const klaiMeili = extractBlock(klaiSrc, MEILI_RE, 'meili', klaiEntrypointPath);
const getklaiMeili = extractBlock(getklaiSrc, MEILI_RE, 'meili', getklaiEntrypointPath);
const klaiFeedback = extractBlock(klaiSrc, FEEDBACK_RE, 'feedback', klaiEntrypointPath);
const getklaiFeedback = extractBlock(getklaiSrc, FEEDBACK_RE, 'feedback', getklaiEntrypointPath);
const klaiStreamCleanup = extractBlock(klaiSrc, STREAM_CLEANUP_RE, 'stream-cleanup', klaiEntrypointPath);
const getklaiStreamCleanup = extractBlock(
  getklaiSrc,
  STREAM_CLEANUP_RE,
  'stream-cleanup',
  getklaiEntrypointPath,
);

// --- sync-guard: both entrypoints must embed byte-identical transform logic ---
if (klaiMeili !== null && getklaiMeili !== null && klaiMeili !== getklaiMeili) {
  fail(
    'meili-sync-guard',
    'klai-entrypoint.sh and getklai/entrypoint.sh Meili transform blocks have drifted apart; keep them byte-identical',
  );
}
if (klaiFeedback !== null && getklaiFeedback !== null && klaiFeedback !== getklaiFeedback) {
  fail(
    'feedback-sync-guard',
    'klai-entrypoint.sh and getklai/entrypoint.sh feedback transform blocks have drifted apart; keep them byte-identical',
  );
}
if (
  klaiStreamCleanup !== null &&
  getklaiStreamCleanup !== null &&
  klaiStreamCleanup !== getklaiStreamCleanup
) {
  fail(
    'stream-cleanup-sync-guard',
    'klai-entrypoint.sh and getklai/entrypoint.sh stream-cleanup transform blocks have drifted apart; keep them byte-identical',
  );
}

const RUNTIME_PATHS = [
  '/app/packages/data-schemas/dist/models/message.cjs',
  '/app/packages/data-schemas/dist/models/convo.cjs',
  '/app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs',
  '/app/packages/data-schemas/dist/index.cjs',
  '/app/api/db/indexSync.js',
  '/app/api/server/routes/messages.js',
  '/app/packages/api/dist/index.cjs',
];

function remapPaths(script) {
  let out = script;
  for (const p of RUNTIME_PATHS) {
    out = out.split(p).join(path.join(extractedRoot, p.slice(1)));
  }
  return out;
}

function runNode(script) {
  return spawnSync(process.execPath, ['-e', script], { encoding: 'utf8' });
}

function nodeCheck(file) {
  return spawnSync(process.execPath, ['--check', file], { encoding: 'utf8' });
}

function extractedPath(p) {
  return path.join(extractedRoot, p.slice(1));
}

// --- Meili dry run: execute the extracted transform against the real,
// image-extracted data-schemas files (whichever dist shape the pinned image
// actually ships), then assert every REQ-4 condition. ---
if (klaiMeili) {
  const bundledPath = extractedPath('/app/packages/data-schemas/dist/index.cjs');
  const legacyMessagePath = extractedPath('/app/packages/data-schemas/dist/models/message.cjs');
  const legacyConvoPath = extractedPath('/app/packages/data-schemas/dist/models/convo.cjs');
  const legacyMongoMeiliPath = extractedPath(
    '/app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs',
  );
  const indexSyncPath = extractedPath('/app/api/db/indexSync.js');

  const legacyPresent = [legacyMessagePath, legacyConvoPath, legacyMongoMeiliPath].filter(
    existsSync,
  ).length;
  const bundledPresent = existsSync(bundledPath);

  // Cover both dist shapes the Meili logic supports: assert shape-detection
  // unambiguously identifies whichever shape THIS image ships (the other
  // shape is exercised via synthetic fixtures in
  // deploy/librechat/tests/meili_tenant_indexes.test.cjs -- a real image can
  // only ever contain one shape at a time).
  if (legacyPresent !== 0 && legacyPresent !== 3) {
    fail(
      'meili-shape',
      `ambiguous pre-rolldown shape in extracted image: ${legacyPresent}/3 per-model files present`,
    );
  } else if (legacyPresent === 0 && !bundledPresent) {
    fail(
      'meili-shape',
      'neither the pre-rolldown per-model files nor the bundled dist/index.cjs were extracted from the image',
    );
  } else if (legacyPresent === 3 && bundledPresent) {
    fail('meili-shape', 'both pre-rolldown and bundled shapes present in extracted image (unexpected)');
  } else {
    ok(
      'meili-shape',
      `detected ${legacyPresent === 3 ? 'pre-rolldown per-model' : 'rolldown-bundled'} data-schemas shape in the image`,
    );
  }

  if (!existsSync(indexSyncPath)) {
    fail('meili', `indexSync.js was not extracted from the image: ${indexSyncPath}`);
  }

  if (!failed) {
    const script = remapPaths(klaiMeili);
    const result = runNode(script);
    if (result.status !== 0) {
      fail(
        'meili',
        `transform execution failed against extracted image files (likely upstream syntax drift):\n${result.stderr}`,
      );
    } else {
      const filesToCheck = [bundledPath, legacyMessagePath, legacyConvoPath, legacyMongoMeiliPath, indexSyncPath].filter(
        existsSync,
      );
      let sawTenantReplacement = false;
      for (const f of filesToCheck) {
        const content = readFileSync(f, 'utf8');
        if (/indexName:\s*['"]messages['"]/.test(content)) {
          fail('meili', `un-tenanted indexName:"messages" literal remains in ${f}`);
        }
        if (/indexName:\s*['"]convos['"]/.test(content)) {
          fail('meili', `un-tenanted indexName:"convos" literal remains in ${f}`);
        }
        if (/client\.index\(['"]messages['"]\)/.test(content)) {
          fail('meili', `un-tenanted client.index("messages") literal remains in ${f}`);
        }
        if (/client\.index\(['"]convos['"]\)/.test(content)) {
          fail('meili', `un-tenanted client.index("convos") literal remains in ${f}`);
        }
        if (
          content.includes("process.env.MEILI_MESSAGES_INDEX || 'messages'") ||
          content.includes("process.env.MEILI_CONVOS_INDEX || 'convos'")
        ) {
          sawTenantReplacement = true;
        }
        const chk = nodeCheck(f);
        if (chk.status !== 0) {
          fail('meili', `node --check failed on transformed ${f}:\n${chk.stderr}`);
        }
      }
      if (!sawTenantReplacement) {
        fail('meili', 'expected tenant-scoped replacement (process.env.MEILI_*_INDEX) not found in any patched file');
      }
      if (!failed) {
        ok(
          'meili',
          `applied cleanly to ${filesToCheck.length} file(s); expected replacement count reached, no forbidden global-index references remain, node --check passed`,
        );
      }
    }
  }
}

// --- Feedback dry run: execute the extracted transform against the real,
// image-extracted messages.js. ---
if (klaiFeedback) {
  const messagesPath = extractedPath('/app/api/server/routes/messages.js');
  if (!existsSync(messagesPath)) {
    fail('feedback', `messages.js was not extracted from the image: ${messagesPath}`);
  } else {
    const script = remapPaths(klaiFeedback);
    const result = runNode(script);
    if (result.status !== 0) {
      fail(
        'feedback',
        `transform execution failed against extracted image messages.js (likely anchor drift after a LibreChat upgrade):\n${result.stderr}`,
      );
    } else {
      const content = readFileSync(messagesPath, 'utf8');
      if (!content.includes('SPEC-KB-015')) {
        fail('feedback', 'expected SPEC-KB-015 marker not found in patched messages.js');
      }
      if (!content.includes('/internal/v1/kb-feedback')) {
        fail('feedback', 'expected kb-feedback forward call not found in patched messages.js');
      }
      const chk = nodeCheck(messagesPath);
      if (chk.status !== 0) {
        fail('feedback', `node --check failed on transformed messages.js:\n${chk.stderr}`);
      }
      if (!failed) {
        ok('feedback', 'applied cleanly; SPEC-KB-015 forward call present, node --check passed');
      }
    }
  }
}

// --- Stream-cleanup dry run: execute the extracted transform against the
// real, image-extracted packages/api bundle (dist/index.cjs). ---
if (klaiStreamCleanup) {
  const streamBundlePath = extractedPath('/app/packages/api/dist/index.cjs');
  if (!existsSync(streamBundlePath)) {
    fail('stream-cleanup', `dist/index.cjs was not extracted from the image: ${streamBundlePath}`);
  } else {
    const script = remapPaths(klaiStreamCleanup);
    const result = runNode(script);
    if (result.status !== 0) {
      fail(
        'stream-cleanup',
        `transform execution failed against extracted image bundle (likely upstream syntax drift):\n${result.stderr}`,
      );
    } else {
      const content = readFileSync(streamBundlePath, 'utf8');
      const replacementCount = (content.match(/cleanupOnComplete: false/g) || []).length;
      if (replacementCount !== 2) {
        fail(
          'stream-cleanup',
          `expected exactly 2 "cleanupOnComplete: false" replacements (Redis-backed + in-memory branch), found ${replacementCount} in ${streamBundlePath} -- a leftover un-patched return means the anchor drifted or only matched partially`,
        );
      }
      if (!content.includes('SPEC-STREAM-CLEANUP-001')) {
        fail('stream-cleanup', 'expected SPEC-STREAM-CLEANUP-001 marker not found in patched bundle');
      }
      const chk = nodeCheck(streamBundlePath);
      if (chk.status !== 0) {
        fail('stream-cleanup', `node --check failed on transformed ${streamBundlePath}:\n${chk.stderr}`);
      }
      if (!failed) {
        ok(
          'stream-cleanup',
          'applied cleanly; both branches carry cleanupOnComplete: false, node --check passed',
        );
      }
    }
  }
}

if (failed) {
  process.exit(1);
}

console.log('DRY-RUN OK: all runtime transforms applied cleanly against files extracted from the pinned image.');
