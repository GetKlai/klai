/**
 * Analyze Command
 *
 * Indexes a repository and stores the knowledge graph in .codeindex/
 */

import path from 'path';
import { execFileSync } from 'child_process';
import v8 from 'v8';
import cliProgress from 'cli-progress';
import { runPipelineFromRepo } from '../core/ingestion/pipeline.js';
import { initKuzu, loadGraphToKuzu, getKuzuStats, executeQuery, executeWithReusedStatement, closeKuzu, createFTSIndex, loadCachedEmbeddings, queryEmbeddingNodeIds } from '../core/lbug/lbug-adapter.js';
// Embedding imports are lazy (dynamic import) so onnxruntime-node is never
// loaded when embeddings are not requested. This avoids crashes on Node
// versions whose ABI is not yet supported by the native binary (#89).
// disposeEmbedder intentionally not called — ONNX Runtime segfaults on cleanup (see #38)
import { getStoragePaths, saveMeta, loadMeta, registerRepo, getGlobalRegistryPath, findRegistryEntry } from '../storage/repo-manager.js';
import { getCurrentCommit, isGitRepo, getGitRoot, getMainRepoRoot, isWorktree, deriveProjectName, getChangedFiles, hasUncommittedChanges } from '../storage/git.js';
import { runIncrementalPipeline, ThresholdExceededError } from '../core/ingestion/incremental-pipeline.js';
import { generateAIContextFiles } from './ai-context.js';
import fs from 'fs/promises';
import { createRequire } from 'module';
import { registerClaudeHook } from './claude-hooks.js';

const require = createRequire(import.meta.url);
const { version: PKG_VERSION } = require('../../package.json');

const isDev = process.env.NODE_ENV === 'development';
const HEAP_MB = 8192;
const HEAP_FLAG = `--max-old-space-size=${HEAP_MB}`;

/** Re-exec the process with an 8GB heap if we're currently below that. */
function ensureHeap(): boolean {
  const nodeOpts = process.env.NODE_OPTIONS || '';
  if (nodeOpts.includes('--max-old-space-size')) return false;

  const v8Heap = v8.getHeapStatistics().heap_size_limit;
  if (v8Heap >= HEAP_MB * 1024 * 1024 * 0.9) return false;

  try {
    execFileSync(process.execPath, [HEAP_FLAG, ...process.argv.slice(1)], {
      stdio: 'inherit',
      env: { ...process.env, NODE_OPTIONS: `${nodeOpts} ${HEAP_FLAG}`.trim() },
    });
  } catch (e: any) {
    // SIGKILL is used intentionally to avoid native library cleanup crashes
    if (e.signal !== 'SIGKILL') {
      process.exitCode = e.status ?? 1;
    }
  }
  return true;
}

export interface AnalyzeOptions {
  force?: boolean;
  embeddings?: boolean; // default true (Commander --no-embeddings sets to false)
  embeddingLimit?: string;
}

/** Default threshold: auto-skip embeddings for repos with more nodes than this.
 *  Override with --embedding-limit <n> or set to 0 for no limit. */
const DEFAULT_EMBEDDING_NODE_LIMIT = 500_000;

const PHASE_LABELS: Record<string, string> = {
  extracting: 'Scanning files',
  structure: 'Building structure',
  parsing: 'Parsing code',
  imports: 'Resolving imports',
  calls: 'Tracing calls',
  heritage: 'Extracting inheritance',
  communities: 'Detecting communities',
  processes: 'Detecting processes',
  complete: 'Pipeline complete',
  lbug: 'Loading into LadybugDB',
  fts: 'Creating search indexes',
  embeddings: 'Generating embeddings',
  done: 'Done',
};

// ANSI color helpers — disabled when stdout is not a TTY (e.g. Claude Code hooks)
const isTTY = process.stdout.isTTY ?? false;
const dim = (s: string): string => isTTY ? `\x1b[2m${s}\x1b[0m` : s;
const bold = (s: string): string => isTTY ? `\x1b[1m${s}\x1b[0m` : s;
const orange = (s: string): string => isTTY ? `\x1b[38;5;209m${s}\x1b[0m` : s;

export const BANNER = [
  ' ██████╗ ██████╗ ██████╗ ███████╗  ██╗███╗   ██╗██████╗ ███████╗██╗  ██╗',
  '██╔════╝██╔═══██╗██╔══██╗██╔════╝  ██║████╗  ██║██╔══██╗██╔════╝╚██╗██╔╝',
  '██║     ██║   ██║██║  ██║█████╗    ██║██╔██╗ ██║██║  ██║█████╗   ╚███╔╝ ',
  '██║     ██║   ██║██║  ██║██╔══╝    ██║██║╚██╗██║██║  ██║██╔══╝   ██╔██╗ ',
  '╚██████╗╚██████╔╝██████╔╝███████╗  ██║██║ ╚████║██████╔╝███████╗██╔╝ ██╗',
  ' ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝  ╚═╝╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝',
].map(line => orange('  ' + line)).join('\n');

export { PKG_VERSION };

export const printBanner = (): void => {
  console.log(`\n${BANNER}\n${dim(`  v${PKG_VERSION}`)}\n`);
};

export const analyzeCommand = async (
  nameArg?: string,
  inputPath?: string,
  options?: AnalyzeOptions & { skipBanner?: boolean }
) => {
  if (ensureHeap()) return;

  if (!options?.skipBanner) {
    printBanner();
  }

  let projectName: string;
  let repoPath: string;

  if (nameArg && inputPath) {
    // Explicit: codeindex analyze ParrotKey ~/repos/myapp
    projectName = nameArg;
    repoPath = path.resolve(inputPath);
  } else if (nameArg && !inputPath) {
    // Could be: codeindex analyze ParrotKey (name only, resolve repo from cwd)
    // Or:       codeindex analyze ~/repos/myapp (path only, resolve name from registry)
    const resolved = path.resolve(nameArg);
    // Check if the resolved path is actually a git repo ROOT (not just inside one).
    // isGitRepo() returns true for any subdirectory inside a repo, which causes
    // false positives on case-insensitive filesystems (e.g. "CodeIndex" matching
    // a "codeindex/" subdirectory). We need the resolved path to be the actual
    // repo/worktree root to treat it as a path argument.
    const gitRoot = getGitRoot(resolved);
    const isRepoRoot = gitRoot !== null && path.resolve(gitRoot) === path.resolve(resolved);
    if (isRepoRoot) {
      // It's a path pointing to a repo root — resolve project name from registry or basename
      repoPath = resolved;
      const entry = await findRegistryEntry(resolved);
      projectName = entry?.name || path.basename(getMainRepoRoot(resolved) || resolved);
    } else {
      // It's a project name — resolve repo from cwd.
      // Use worktree root (where code lives), not main repo root (may be bare).
      projectName = nameArg;
      const cwdGitRoot = getGitRoot(process.cwd());
      const mainRoot = getMainRepoRoot(process.cwd());
      repoPath = cwdGitRoot || mainRoot || '';
    }
  } else {
    // No arguments: auto-detect from cwd.
    // For worktrees: use the worktree root (where the code lives), not the main
    // repo root (which may be a bare checkout with no source files).
    const gitRoot = getGitRoot(process.cwd());
    const mainRoot = getMainRepoRoot(process.cwd());
    repoPath = gitRoot || mainRoot || '';

    if (!repoPath) {
      console.log('  Not inside a git repository\n');
      process.exitCode = 1;
      return;
    }

    // Look up existing registry entry for this repo
    const entry = await findRegistryEntry(process.cwd());
    if (entry) {
      projectName = entry.name;
    } else {
      projectName = deriveProjectName(process.cwd());
    }
  }

  if (!repoPath || !isGitRepo(repoPath)) {
    console.log('  Not a git repository\n');
    process.exitCode = 1;
    return;
  }

  // For registry lookups: always store the main repo root so all worktrees share the same index entry.
  // For file walking: use repoPath (which may be a worktree with the actual source code).
  const registryPath = getMainRepoRoot(repoPath) || repoPath;

  console.log(`  ${dim('project')}  ${bold(projectName)}`);
  console.log(`  ${dim('path')}     ${repoPath}`);
  if (isWorktree(process.cwd())) {
    const mainRoot = getMainRepoRoot(process.cwd());
    if (mainRoot) console.log(`  ${dim('repo')}     ${mainRoot}`);
    const worktreeName = path.basename(getGitRoot(process.cwd()) || '');
    console.log(`  ${dim('worktree')} ${worktreeName}`);
  }
  const derivedName = deriveProjectName(process.cwd());
  if (projectName !== derivedName) {
    console.log(`  ${dim('tip')}      rename with \`codeindex rename ${derivedName}\``);
  }
  console.log('');

  const { storagePath, lbugPath } = getStoragePaths(projectName);
  const currentCommit = getCurrentCommit(repoPath);
  const existingMeta = await loadMeta(storagePath);

  if (existingMeta && !options?.force && existingMeta.lastCommit === currentCommit && !hasUncommittedChanges(repoPath)) {
    console.log('  Already up to date\n');
    return;
  }

  // ── Incremental update path ─────────────────────────────────────────
  // If we have a previous commit, try incremental update before full rebuild.
  // LadybugDB stores the database as a single file (not a directory).
  let lbugPathValid = false;
  try {
    await fs.stat(lbugPath);
    lbugPathValid = true; // exists as file or directory — both valid
  } catch { /* doesn't exist */ }

  const canIncremental = existingMeta?.lastCommit && !options?.force && currentCommit && lbugPathValid;
  if (canIncremental) {
    // Hoist bar and console refs so the catch block can always clean up
    let incBar: cliProgress.SingleBar | null = null;
    const origLogInc = console.log.bind(console);
    const origWarnInc = console.warn.bind(console);
    const origErrorInc = console.error.bind(console);

    const restoreConsole = () => {
      console.log = origLogInc;
      console.warn = origWarnInc;
      console.error = origErrorInc;
    };

    try {
      const changedFiles = getChangedFiles(existingMeta.lastCommit!, currentCommit, repoPath);
      const totalChanges = changedFiles.added.length + changedFiles.modified.length + changedFiles.deleted.length;

      if (totalChanges === 0) {
        // Commit may have advanced (e.g. merge, docs-only) but no indexable files changed.
        // Update stored commit so next run hits the fast early exit.
        if (existingMeta.lastCommit !== currentCommit) {
          const meta = {
            ...existingMeta,
            lastCommit: currentCommit,
            indexedAt: new Date().toISOString(),
          };
          await saveMeta(storagePath, meta);
          await registerRepo(projectName, registryPath, meta);
          console.log('  No indexable changes. Updated commit pointer.\n');
        } else {
          console.log('  Already up to date\n');
        }
        return;
      }

      if (totalChanges > 0) {
        console.log(`  Incremental update: ${totalChanges} files changed\n`);

        incBar = new cliProgress.SingleBar({
          format: '  {bar} {percentage}% | {phase}',
          barCompleteChar: '\u2588',
          barIncompleteChar: '\u2591',
          hideCursor: true,
          barGlue: '',
          autopadding: true,
          clearOnComplete: false,
          stopOnComplete: false,
        }, cliProgress.Presets.shades_grey);

        incBar.start(100, 0, { phase: 'Starting incremental update...' });

        const incLog = (...args: any[]) => {
          if (isTTY) process.stdout.write('\x1b[2K\r');
          origLogInc(args.map(a => (typeof a === 'string' ? a : String(a))).join(' '));
        };
        console.log = incLog;
        console.warn = incLog;
        console.error = incLog;

        const t0Inc = Date.now();
        const incResult = await runIncrementalPipeline(
          repoPath,
          changedFiles,
          lbugPath,
          (progress) => {
            const label = PHASE_LABELS[progress.phase] || progress.message || progress.phase;
            incBar!.update(progress.percent, { phase: label });
          },
        );

        incBar.update(100, { phase: 'Incremental update complete' });
        incBar.stop();
        incBar = null;
        restoreConsole();

        // ── Embeddings for incremental ──────────────────────────────────
        // Use lightweight nodeId-only query (no vectors loaded)
        const skipEmbeddingNodeIds = await queryEmbeddingNodeIds();

        const incStats = await getKuzuStats();
        let incEmbeddingTime = '0.0';
        let incEmbeddingSkipped = options?.embeddings === false;
        let incEmbeddingSkipReason = 'disabled (--no-embeddings)';

        const incEmbeddingNodeLimit = options?.embeddingLimit
          ? parseInt(options.embeddingLimit, 10)
          : DEFAULT_EMBEDDING_NODE_LIMIT;

        if (!incEmbeddingSkipped && incStats.nodes === 0) {
          incEmbeddingSkipped = true;
          incEmbeddingSkipReason = 'skipped (0 nodes)';
        } else if (!incEmbeddingSkipped && incEmbeddingNodeLimit > 0 && incStats.nodes > incEmbeddingNodeLimit) {
          incEmbeddingSkipped = true;
          incEmbeddingSkipReason = `skipped (${incStats.nodes} > ${incEmbeddingNodeLimit} limit)`;
        }

        // Skip embedding model load for small incremental changes (< 500 new nodes)
        // The model takes 12+ seconds to load — not worth it for a handful of nodes.
        if (!incEmbeddingSkipped) {
          if (incResult.nodesInserted < 500) {
            incEmbeddingSkipped = true;
            incEmbeddingSkipReason = `deferred (${incResult.nodesInserted} new nodes < 500 threshold)`;
          }
        }

        // Suppress ONNX native stderr warnings BEFORE any embedding activity
        // (must be installed early — ONNX C++ layer writes directly to fd 2)
        const origStderrWriteInc = process.stderr.write.bind(process.stderr);
        process.stderr.write = ((chunk: unknown, ...rest: unknown[]) => {
          const str = typeof chunk === 'string' ? chunk : String(chunk);
          if (str.includes('content-length') || str.includes('expand buffer')) {
            return true;
          }
          return (origStderrWriteInc as Function)(chunk, ...rest);
        }) as typeof process.stderr.write;

        if (!incEmbeddingSkipped) {
          console.log('');
          const embBar = new cliProgress.SingleBar({
            format: '  {bar} {percentage}% | {phase}',
            barCompleteChar: '\u2588',
            barIncompleteChar: '\u2591',
            hideCursor: true,
            barGlue: '',
            autopadding: true,
            clearOnComplete: false,
            stopOnComplete: false,
          }, cliProgress.Presets.shades_grey);

          embBar.start(100, 0, { phase: 'Loading embedding model...' });

          const embLogInc = (...args: unknown[]) => {
            const msg = args.map(a => (typeof a === 'string' ? a : String(a))).join(' ');
            if (msg.includes('content-length') || msg.includes('expand buffer')) return;
            if (isTTY) process.stdout.write('\x1b[2K\r');
            origLogInc(msg);
          };
          console.log = embLogInc;
          console.warn = embLogInc;
          console.error = embLogInc;

          const t0EmbInc = Date.now();
          const { runEmbeddingPipeline } = await import('../core/embeddings/embedding-pipeline.js');
          await runEmbeddingPipeline(
            executeQuery,
            executeWithReusedStatement,
            (progress) => {
              const label = progress.phase === 'loading-model'
                ? 'Loading embedding model...'
                : progress.phase === 'indexing'
                  ? 'Creating vector index...'
                  : `Embedding ${progress.nodesProcessed || 0}/${progress.totalNodes || '?'}`;
              embBar.update(progress.percent, { phase: label });
            },
            {},
            skipEmbeddingNodeIds,
          );
          incEmbeddingTime = ((Date.now() - t0EmbInc) / 1000).toFixed(1);

          embBar.update(100, { phase: 'Embeddings complete' });
          embBar.stop();
          restoreConsole();
        }

        // Restore stderr suppression (installed before embedding check)
        process.stderr.write = origStderrWriteInc;

        // Save metadata and finalize
        const incMeta = {
          repoPath,
          lastCommit: currentCommit,
          indexedAt: new Date().toISOString(),
          stats: {
            files: existingMeta.stats?.files || 0,
            nodes: incStats.nodes,
            edges: incStats.edges,
            communities: incResult.communities || existingMeta.stats?.communities,
            processes: incResult.processes || existingMeta.stats?.processes,
          },
        };
        await saveMeta(storagePath, incMeta);
        await registerRepo(projectName, registryPath, incMeta);

        const hookResult = await registerClaudeHook();
        const aiContext = await generateAIContextFiles(repoPath, storagePath, projectName, {
          files: existingMeta.stats?.files || 0,
          nodes: incStats.nodes,
          edges: incStats.edges,
          communities: incMeta.stats.communities,
          processes: incMeta.stats.processes,
        });

        await closeKuzu();

        const incTotalTime = ((Date.now() - t0Inc) / 1000).toFixed(1);
        console.log(`\n  Incremental update complete (${incTotalTime}s)\n`);
        console.log(`  +${incResult.added} added | ~${incResult.modified} modified | -${incResult.deleted} deleted`);
        console.log(`  ${incResult.nodesInserted} nodes inserted | ${incResult.nodesDeleted} nodes deleted | ${incResult.edgesInserted} edges inserted`);
        console.log(`  ${incStats.nodes.toLocaleString()} total nodes | ${incStats.edges.toLocaleString()} total edges`);
        console.log(`  Embeddings ${incEmbeddingSkipped ? incEmbeddingSkipReason : incEmbeddingTime + 's'}`);

        if (aiContext.files.length > 0) {
          console.log(`  Context: ${aiContext.files.join(', ')}`);
        }
        if (hookResult.registered) {
          console.log(`  Hooks: ${hookResult.message}`);
        }
        console.log('');

        // Force-kill to avoid native library cleanup crashes (same as full rebuild path).
        // LadybugDB's native resources keep the event loop alive even after closeKuzu().
        setTimeout(() => process.kill(process.pid, 'SIGKILL'), 100);

        return;
      }
    } catch (e: any) {
      // CRITICAL: stop the progress bar and restore console BEFORE logging.
      // Otherwise all output goes through the dead bar and is invisible.
      try { if (incBar) { incBar.stop(); incBar = null; } } catch {}
      restoreConsole();

      if (e?.code === 'DATABASE_LOCKED') {
        // Database is locked by another process — don't fall through to full rebuild
        try { await closeKuzu(); } catch {}
        console.warn(
          `\n⚠️  Database is locked by another process (probably the MCP server).\n` +
          `   The index will be updated automatically next time the database is free.\n` +
          `   To force: close Claude Code, then run \`codeindex analyze\`.\n`
        );
        return;
      }

      if (e instanceof ThresholdExceededError) {
        console.log(`  Too many changes (${e.changedCount} files). CSV bulk load is faster.`);
      } else {
        console.log(`  Incremental update failed: ${e.message}`);
        if (isDev) {
          console.log(`  Error: ${e.stack || e.message}\n`);
        }
      }
      console.log('  Falling back to full rebuild...\n');
      // Fall through to full rebuild
      try { await closeKuzu(); } catch {}
    }
  }

  // Single progress bar for entire pipeline
  const bar = new cliProgress.SingleBar({
    format: '  {bar} {percentage}% | {phase}',
    barCompleteChar: '\u2588',
    barIncompleteChar: '\u2591',
    hideCursor: true,
    barGlue: '',
    autopadding: true,
    clearOnComplete: false,
    stopOnComplete: false,
  }, cliProgress.Presets.shades_grey);

  bar.start(100, 0, { phase: 'Initializing...' });

  // Graceful SIGINT handling — clean up resources and exit
  let aborted = false;
  const sigintHandler = () => {
    if (aborted) process.exit(1); // Second Ctrl-C: force exit
    aborted = true;
    bar.stop();
    console.log('\n  Interrupted — cleaning up...');
    closeKuzu().catch(() => {}).finally(() => process.exit(130));
  };
  process.on('SIGINT', sigintHandler);

  // Route all console output through bar.log() so the bar doesn't stamp itself
  // multiple times when other code writes to stdout/stderr mid-render.
  const origLog = console.log.bind(console);
  const origWarn = console.warn.bind(console);
  const origError = console.error.bind(console);
  const barLog = (...args: any[]) => {
    // Clear the bar line, print the message, then let the next bar.update redraw
    if (isTTY) process.stdout.write('\x1b[2K\r');
    origLog(args.map(a => (typeof a === 'string' ? a : String(a))).join(' '));
  };
  console.log = barLog;
  console.warn = barLog;
  console.error = barLog;

  // Track elapsed time per phase — both updateBar and the interval use the
  // same format so they don't flicker against each other.
  let lastPhaseLabel = 'Initializing...';
  let phaseStart = Date.now();

  /** Update bar with phase label + elapsed seconds (shown after 3s). */
  const updateBar = (value: number, phaseLabel: string) => {
    if (phaseLabel !== lastPhaseLabel) { lastPhaseLabel = phaseLabel; phaseStart = Date.now(); }
    const elapsed = Math.round((Date.now() - phaseStart) / 1000);
    const display = elapsed >= 3 ? `${phaseLabel} (${elapsed}s)` : phaseLabel;
    bar.update(value, { phase: display });
  };

  // Tick elapsed seconds for phases with infrequent progress callbacks
  // (e.g. CSV streaming, FTS indexing). Uses the same display format as
  // updateBar so there's no flickering.
  const elapsedTimer = setInterval(() => {
    const elapsed = Math.round((Date.now() - phaseStart) / 1000);
    if (elapsed >= 3) {
      bar.update({ phase: `${lastPhaseLabel} (${elapsed}s)` });
    }
  }, 1000);

  const t0Global = Date.now();

  // ── Cache embeddings from existing index before rebuild ────────────
  let cachedEmbeddingNodeIds = new Set<string>();
  let cachedEmbeddings: Array<{ nodeId: string; embedding: number[] }> = [];

  if (options?.embeddings !== false && existingMeta && !options?.force) {
    try {
      updateBar(0, 'Caching embeddings...');
      await initKuzu(lbugPath);
      const cached = await loadCachedEmbeddings();
      cachedEmbeddingNodeIds = cached.embeddingNodeIds;
      cachedEmbeddings = cached.embeddings;
      await closeKuzu();
    } catch {
      try { await closeKuzu(); } catch {}
    }
  }

  // ── Phase 1: Full Pipeline (0–60%) ─────────────────────────────────
  const pipelineResult = await runPipelineFromRepo(repoPath, (progress) => {
    const phaseLabel = progress.message && progress.percent === 0 && progress.phase === 'extracting'
      ? progress.message
      : PHASE_LABELS[progress.phase] || progress.phase;
    const scaled = Math.round(progress.percent * 0.6);
    updateBar(scaled, phaseLabel);
  });

  // ── Phase 2: LadybugDB (60–85%) ──────────────────────────────────
  updateBar(60, 'Loading into LadybugDB...');

  await closeKuzu();
  const lbugFiles = [lbugPath, `${lbugPath}.wal`, `${lbugPath}.lock`];
  for (const f of lbugFiles) {
    try { await fs.rm(f, { recursive: true, force: true }); } catch {}
  }

  const t0Lbug = Date.now();
  await initKuzu(lbugPath);
  let lbugMsgCount = 0;
  const lbugResult = await loadGraphToKuzu(pipelineResult.graph, pipelineResult.repoPath, storagePath, (msg) => {
    lbugMsgCount++;
    const progress = Math.min(84, 60 + Math.round((lbugMsgCount / (lbugMsgCount + 10)) * 24));
    updateBar(progress, msg);
  });
  const lbugTime = ((Date.now() - t0Lbug) / 1000).toFixed(1);
  const lbugWarnings = lbugResult.warnings;

  // ── Phase 3: FTS (85–90%) ─────────────────────────────────────────
  updateBar(85, 'Creating search indexes...');

  const t0Fts = Date.now();
  try {
    await createFTSIndex('File', 'file_fts', ['name', 'content']);
    await createFTSIndex('Function', 'function_fts', ['name', 'content']);
    await createFTSIndex('Class', 'class_fts', ['name', 'content']);
    await createFTSIndex('Method', 'method_fts', ['name', 'content']);
    await createFTSIndex('Interface', 'interface_fts', ['name', 'content']);
  } catch (e: any) {
    // Non-fatal — FTS is best-effort
  }
  const ftsTime = ((Date.now() - t0Fts) / 1000).toFixed(1);

  // ── Phase 3.5: Re-insert cached embeddings ────────────────────────
  if (cachedEmbeddings.length > 0) {
    updateBar(88, `Restoring ${cachedEmbeddings.length} cached embeddings...`);
    const EMBED_BATCH = 200;
    for (let i = 0; i < cachedEmbeddings.length; i += EMBED_BATCH) {
      const batch = cachedEmbeddings.slice(i, i + EMBED_BATCH);
      const paramsList = batch.map(e => ({ nodeId: e.nodeId, embedding: e.embedding }));
      try {
        await executeWithReusedStatement(
          `CREATE (e:CodeEmbedding {nodeId: $nodeId, embedding: $embedding})`,
          paramsList,
        );
      } catch { /* some may fail if node was removed, that's fine */ }
    }
  }

  // ── Phase 4: Embeddings (separate progress bar) ─────────────────
  const stats = await getKuzuStats();
  let embeddingTime = '0.0';
  let embeddingSkipped = true;
  let embeddingSkipReason = 'disabled (--no-embeddings)';

  const embeddingNodeLimit = options?.embeddingLimit
    ? parseInt(options.embeddingLimit, 10)
    : DEFAULT_EMBEDDING_NODE_LIMIT;

  if (options?.embeddings !== false) {
    if (stats.nodes === 0) {
      embeddingSkipReason = 'skipped (0 nodes)';
    } else if (embeddingNodeLimit > 0 && stats.nodes > embeddingNodeLimit) {
      embeddingSkipReason = `skipped (${stats.nodes.toLocaleString()} nodes > ${embeddingNodeLimit.toLocaleString()} limit, use --embedding-limit to raise)`;
    } else {
      embeddingSkipped = false;
    }
  }

  // Suppress native ONNX warnings (content-length, etc.) BEFORE any embedding activity.
  // ONNX Runtime writes directly to stderr from C++, bypassing console.warn.
  const origStderrWrite = process.stderr.write.bind(process.stderr);
  process.stderr.write = ((chunk: unknown, ...rest: unknown[]) => {
    const str = typeof chunk === 'string' ? chunk : String(chunk);
    if (str.includes('content-length') || str.includes('expand buffer')) {
      return true;
    }
    return (origStderrWrite as Function)(chunk, ...rest);
  }) as typeof process.stderr.write;

  if (!embeddingSkipped) {
    // Complete the main bar and start a fresh one for embeddings
    updateBar(100, 'Indexing complete');
    bar.stop();
    console.log = origLog;
    console.warn = origWarn;
    console.error = origError;
    console.log('');

    // New progress bar for embeddings
    const embBar = new cliProgress.SingleBar({
      format: '  {bar} {percentage}% | {phase}',
      barCompleteChar: '\u2588',
      barIncompleteChar: '\u2591',
      hideCursor: true,
      barGlue: '',
      autopadding: true,
      clearOnComplete: false,
      stopOnComplete: false,
    }, cliProgress.Presets.shades_grey);

    embBar.start(100, 0, { phase: 'Loading embedding model...' });

    const embLog = (...args: any[]) => {
      const msg = args.map(a => (typeof a === 'string' ? a : String(a))).join(' ');
      if (msg.includes('content-length') || msg.includes('expand buffer')) return;
      if (isTTY) process.stdout.write('\x1b[2K\r');
      origLog(msg);
    };
    console.log = embLog;
    console.warn = embLog;
    console.error = embLog;

    let embPhaseStart = Date.now();
    let embLastLabel = 'Loading embedding model...';

    const updateEmbBar = (value: number, phaseLabel: string) => {
      if (phaseLabel !== embLastLabel) { embLastLabel = phaseLabel; embPhaseStart = Date.now(); }
      const elapsed = Math.round((Date.now() - embPhaseStart) / 1000);
      const display = elapsed >= 3 ? `${phaseLabel} (${elapsed}s)` : phaseLabel;
      embBar.update(value, { phase: display });
    };

    const embTimer = setInterval(() => {
      const elapsed = Math.round((Date.now() - embPhaseStart) / 1000);
      if (elapsed >= 3) {
        embBar.update({ phase: `${embLastLabel} (${elapsed}s)` });
      }
    }, 1000);

    const t0Emb = Date.now();
    const { runEmbeddingPipeline } = await import('../core/embeddings/embedding-pipeline.js');
    await runEmbeddingPipeline(
      executeQuery,
      executeWithReusedStatement,
      (progress) => {
        const label = progress.phase === 'loading-model'
          ? 'Loading embedding model...'
          : progress.phase === 'indexing'
            ? 'Creating vector index...'
            : `Embedding ${progress.nodesProcessed || 0}/${progress.totalNodes || '?'}`;
        updateEmbBar(progress.percent, label);
      },
      {},
      cachedEmbeddingNodeIds.size > 0 ? cachedEmbeddingNodeIds : undefined,
    );
    embeddingTime = ((Date.now() - t0Emb) / 1000).toFixed(1);

    clearInterval(embTimer);
    embBar.update(100, { phase: 'Embeddings complete' });
    embBar.stop();

    process.stderr.write = origStderrWrite;
    console.log = origLog;
    console.warn = origWarn;
    console.error = origError;
  }

  // Restore stderr (was suppressed for ONNX warnings)
  process.stderr.write = origStderrWrite;

  // ── Phase 5: Finalize ──────────────────────────────────────────
  if (embeddingSkipped) {
    updateBar(98, 'Saving metadata...');
  }

  // Count embeddings in the index (cached + newly generated)
  let embeddingCount = 0;
  try {
    const embResult = await executeQuery(`MATCH (e:CodeEmbedding) RETURN count(e) AS cnt`);
    embeddingCount = embResult?.[0]?.cnt ?? 0;
  } catch { /* table may not exist if embeddings never ran */ }

  const meta = {
    repoPath,
    lastCommit: currentCommit,
    indexedAt: new Date().toISOString(),
    stats: {
      files: pipelineResult.totalFileCount,
      nodes: stats.nodes,
      edges: stats.edges,
      communities: pipelineResult.communityResult?.stats.totalCommunities,
      processes: pipelineResult.processResult?.stats.totalProcesses,
      embeddings: embeddingCount,
    },
  };
  await saveMeta(storagePath, meta);
  await registerRepo(projectName, registryPath, meta);

  // First-time indexing: scan project context (README, git log, structure)
  if (!existingMeta) {
    try {
      const { scanProjectContext } = await import('./project-scanner.js');
      const scanned = await scanProjectContext(repoPath, projectName);
      if (scanned > 0 && isDev) {
        console.log(`  📝 Saved ${scanned} project observations to memory`);
      }
    } catch {
      // Non-fatal — project scanning is best-effort
    }
  }

  const hookResult = await registerClaudeHook();
  let aggregatedClusterCount = 0;
  if (pipelineResult.communityResult?.communities) {
    const groups = new Map<string, number>();
    for (const c of pipelineResult.communityResult.communities) {
      const label = c.heuristicLabel || c.label || 'Unknown';
      groups.set(label, (groups.get(label) || 0) + c.symbolCount);
    }
    aggregatedClusterCount = Array.from(groups.values()).filter(count => count >= 5).length;
  }

  const aiContext = await generateAIContextFiles(repoPath, storagePath, projectName, {
    files: pipelineResult.totalFileCount,
    nodes: stats.nodes,
    edges: stats.edges,
    communities: pipelineResult.communityResult?.stats.totalCommunities,
    clusters: aggregatedClusterCount,
    processes: pipelineResult.processResult?.stats.totalProcesses,
  });

  await closeKuzu();
  // Note: we intentionally do NOT call disposeEmbedder() here.
  // ONNX Runtime's native cleanup segfaults on macOS and some Linux configs.
  // Since the process exits immediately after, Node.js reclaims everything.

  const totalTime = ((Date.now() - t0Global) / 1000).toFixed(1);

  clearInterval(elapsedTimer);
  process.removeListener('SIGINT', sigintHandler);

  // If embeddings ran, the main bar is already stopped and console restored.
  // Only finalize the main bar if embeddings were skipped.
  if (embeddingSkipped) {
    console.log = origLog;
    console.warn = origWarn;
    console.error = origError;

    bar.update(100, { phase: 'Done' });
    bar.stop();
  }

  // ── Summary ───────────────────────────────────────────────────────
  const embeddingsCached = cachedEmbeddings.length > 0;
  console.log(`\n  ${bold(projectName)} indexed successfully (${totalTime}s)${embeddingsCached ? ` [${cachedEmbeddings.length} embeddings cached]` : ''}\n`);
  console.log(`  ${stats.nodes.toLocaleString()} nodes | ${stats.edges.toLocaleString()} edges | ${pipelineResult.communityResult?.stats.totalCommunities || 0} clusters | ${pipelineResult.processResult?.stats.totalProcesses || 0} flows`);
  console.log(`  LadybugDB ${lbugTime}s | FTS ${ftsTime}s | Embeddings ${embeddingSkipped ? embeddingSkipReason : embeddingTime + 's'}`);

  if (aiContext.files.length > 0) {
    console.log(`  Context: ${aiContext.files.join(', ')}`);
  }

  if (hookResult.registered) {
    console.log(`  Hooks: ${hookResult.message}`);
  }

  // Show a quiet summary if some edge types needed fallback insertion
  if (lbugWarnings.length > 0) {
    const totalFallback = lbugWarnings.reduce((sum, w) => {
      const m = w.match(/\((\d+) edges\)/);
      return sum + (m ? parseInt(m[1]) : 0);
    }, 0);
    console.log(`  Note: ${totalFallback} edges across ${lbugWarnings.length} types inserted via fallback (schema will be updated in next release)`);
  }

  try {
    await fs.access(getGlobalRegistryPath());
  } catch {
    console.log('\n  Tip: Run `codeindex setup` to configure MCP for your editor.');
  }

  console.log('');

  // Force-kill to avoid native library cleanup crashes:
  // - ONNX Runtime registers atexit hooks that segfault on macOS (#38, #40)
  // - LadybugDB's C++ mutex destructor can throw "mutex lock failed: Invalid argument"
  // process.exit() still runs C++ atexit hooks which cause the mutex crash.
  // SIGKILL bypasses all cleanup — safe since closeKuzu() already flushed data.
  setTimeout(() => process.kill(process.pid, 'SIGKILL'), 100);
};
