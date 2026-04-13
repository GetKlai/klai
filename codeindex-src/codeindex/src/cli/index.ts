#!/usr/bin/env node

// Raise Node heap limit for large repos (e.g. Linux kernel).
// Must run before any heavy allocation. If already set by the user, respect it.
if (!process.env.NODE_OPTIONS?.includes('--max-old-space-size')) {
  const execArgv = process.execArgv.join(' ');
  if (!execArgv.includes('--max-old-space-size')) {
    // Re-spawn with a larger heap (8 GB)
    const { execFileSync } = await import('node:child_process');
    try {
      execFileSync(process.execPath, ['--max-old-space-size=8192', ...process.argv.slice(1)], {
        stdio: 'inherit',
        env: { ...process.env, NODE_OPTIONS: `${process.env.NODE_OPTIONS || ''} --max-old-space-size=8192`.trim() },
      });
      process.exit(0);
    } catch (e: any) {
      // If the child was killed by SIGKILL (used by analyze.ts to avoid native
      // library cleanup crashes), treat it as success. Otherwise propagate the
      // child's exit code.
      if (e.signal === 'SIGKILL') {
        process.exit(0);
      }
      process.exit(e.status ?? 1);
    }
  }
}

import { Command } from 'commander';
import { analyzeCommand, printBanner } from './analyze.js';
import { serveCommand } from './serve.js';
import { listCommand } from './list.js';
import { statusCommand } from './status.js';
import { mcpCommand } from './mcp.js';
import { cleanCommand } from './clean.js';
import { setupCommand } from './setup.js';
import { augmentCommand } from './augment.js';
import { wikiCommand } from './wiki.js';
import { renameCommand } from './rename.js';
import { updateCommand } from './update.js';
import { queryCommand, contextCommand, impactCommand, cypherCommand } from './tool.js';
import { evalServerCommand } from './eval-server.js';
import { memoryContextCommand } from './memory-context.js';
import {
  learningsCommand, dosCommand, dontsCommand, preferencesCommand,
  decisionsCommand, bugsCommand, patternsCommand, memoryCommand, noteCommand,
} from './memory.js';
const program = new Command();

const isTTY = process.stdout.isTTY ?? false;
const orange = (s: string): string => isTTY ? `\x1b[38;5;209m${s}\x1b[0m` : s;
const BANNER = [
  ' ██████╗ ██████╗ ██████╗ ███████╗  ██╗███╗   ██╗██████╗ ███████╗██╗  ██╗',
  '██╔════╝██╔═══██╗██╔══██╗██╔════╝  ██║████╗  ██║██╔══██╗██╔════╝╚██╗██╔╝',
  '██║     ██║   ██║██║  ██║█████╗    ██║██╔██╗ ██║██║  ██║█████╗   ╚███╔╝ ',
  '██║     ██║   ██║██║  ██║██╔══╝    ██║██║╚██╗██║██║  ██║██╔══╝   ██╔██╗ ',
  '╚██████╗╚██████╔╝██████╔╝███████╗  ██║██║ ╚████║██████╔╝███████╗██╔╝ ██╗',
  ' ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝  ╚═╝╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝',
].map(line => orange('  ' + line)).join('\n');

program
  .name('codeindex')
  .description('CodeIndex local CLI and MCP server')
  .version('1.3.60')
  .addHelpText('beforeAll', '\n' + BANNER + '\n');

program
  .command('setup')
  .description('One-time setup: configure MCP, hooks, and skills for Cursor, Claude Code, OpenCode')
  .option('--uninstall', 'Remove all CodeIndex configuration from editors')
  .action(setupCommand);

program
  .command('analyze [name] [path]')
  .description('Index a repository. First time: codeindex analyze MyProject ~/path/to/repo. Update: codeindex update (from any worktree)')
  .option('-f, --force', 'Force full re-index even if up to date')
  .option('--no-embeddings', 'Disable embedding generation (embeddings are on by default)')
  .option('--embedding-limit <n>', 'Max nodes for embeddings (default: 500000, 0 = no limit)')
  .action(analyzeCommand);

program
  .command('update')
  .description('Re-index the current repository (must be previously indexed)')
  .option('-f, --force', 'Force full re-index even if up to date')
  .option('--no-embeddings', 'Disable embedding generation (embeddings are on by default)')
  .option('--embedding-limit <n>', 'Max nodes for embeddings (default: 500000, 0 = no limit)')
  .action(updateCommand);

program
  .command('serve')
  .description('Start local HTTP server for web UI connection')
  .option('-p, --port <port>', 'Port number', '4747')
  .option('--host <host>', 'Bind address (default: 127.0.0.1, use 0.0.0.0 for remote access)')
  .action(serveCommand);

program
  .command('mcp')
  .description('Start MCP server (stdio) — serves all indexed repos')
  .action(mcpCommand);

program
  .command('list')
  .description('List all indexed repositories')
  .action(listCommand);

program
  .command('status')
  .description('Show index status for current repo')
  .action(statusCommand);

program
  .command('clean')
  .description('Delete CodeIndex index for current repo')
  .option('-f, --force', 'Skip confirmation prompt')
  .option('--all', 'Clean all indexed repos')
  .action(cleanCommand);

program
  .command('rename <name> [new-name]')
  .description('Rename a project (e.g. codeindex rename NewName)')
  .action(renameCommand);

program
  .command('wiki [path]')
  .description('Generate repository wiki from knowledge graph')
  .option('-f, --force', 'Force full regeneration even if up to date')
  .option('--model <model>', 'LLM model name (default: minimax/minimax-m2.5)')
  .option('--base-url <url>', 'LLM API base URL (default: OpenAI)')
  .option('--api-key <key>', 'LLM API key (saved to ~/.codeindex/config.json)')
  .option('--concurrency <n>', 'Parallel LLM calls (default: 3)', '3')
  .option('--gist', 'Publish wiki as a public GitHub Gist after generation')
  .action(wikiCommand);

program
  .command('augment <pattern>')
  .description('Augment a search pattern with knowledge graph context (used by hooks)')
  .action(augmentCommand);

program
  .command('memory-context [project]')
  .description('Output recent memory observations for hook injection (used by hooks)')
  .action(memoryContextCommand);

program
  .command('dismiss')
  .description('Dismiss CodeIndex suggestions for the current repo')
  .option('--undo', 'Re-enable suggestions for the current repo')
  .action(async (opts: { undo?: boolean }) => {
    const { isGitRepo, getMainRepoRoot } = await import('../storage/git.js');
    const fsp = await import('fs/promises');
    const pathMod = await import('path');
    const osMod = await import('os');

    if (!isGitRepo(process.cwd())) {
      console.log('  Not a git repository.');
      process.exit(1);
    }

    const repoRoot = getMainRepoRoot(process.cwd());
    const dismissedPath = pathMod.default.join(osMod.default.homedir(), '.codeindex', 'dismissed.json');

    let dismissed: string[] = [];
    try {
      dismissed = JSON.parse(await fsp.default.readFile(dismissedPath, 'utf-8'));
      if (!Array.isArray(dismissed)) dismissed = [];
    } catch {}

    const resolved = pathMod.default.resolve(repoRoot);

    if (opts.undo) {
      dismissed = dismissed.filter(d => pathMod.default.resolve(d) !== resolved);
      await fsp.default.mkdir(pathMod.default.dirname(dismissedPath), { recursive: true });
      await fsp.default.writeFile(dismissedPath, JSON.stringify(dismissed, null, 2) + '\n');
      console.log(`  CodeIndex suggestions re-enabled for this repo.`);
    } else {
      if (!dismissed.some(d => pathMod.default.resolve(d) === resolved)) {
        dismissed.push(repoRoot);
      }
      await fsp.default.mkdir(pathMod.default.dirname(dismissedPath), { recursive: true });
      await fsp.default.writeFile(dismissedPath, JSON.stringify(dismissed, null, 2) + '\n');
      console.log(`  CodeIndex suggestions dismissed for this repo.`);
      console.log(`  Run "codeindex dismiss --undo" to re-enable.`);
    }
  });

// ─── Direct Tool Commands (no MCP overhead) ────────────────────────
// These invoke LocalBackend directly for use in eval, scripts, and CI.

program
  .command('query <search_query>')
  .description('Search the knowledge graph for execution flows related to a concept')
  .option('-r, --repo <name>', 'Target repository (omit if only one indexed)')
  .option('-c, --context <text>', 'Task context to improve ranking')
  .option('-g, --goal <text>', 'What you want to find')
  .option('-l, --limit <n>', 'Max processes to return (default: 5)')
  .option('--content', 'Include full symbol source code')
  .action(queryCommand);

program
  .command('context [name]')
  .description('360-degree view of a code symbol: callers, callees, processes')
  .option('-r, --repo <name>', 'Target repository')
  .option('-u, --uid <uid>', 'Direct symbol UID (zero-ambiguity lookup)')
  .option('-f, --file <path>', 'File path to disambiguate common names')
  .option('--content', 'Include full symbol source code')
  .action(contextCommand);

program
  .command('impact <target>')
  .description('Blast radius analysis: what breaks if you change a symbol')
  .option('-d, --direction <dir>', 'upstream (dependants) or downstream (dependencies)', 'upstream')
  .option('-r, --repo <name>', 'Target repository')
  .option('--depth <n>', 'Max relationship depth (default: 3)')
  .option('--include-tests', 'Include test files in results')
  .action(impactCommand);

program
  .command('cypher <query>')
  .description('Execute raw Cypher query against the knowledge graph')
  .option('-r, --repo <name>', 'Target repository')
  .action(cypherCommand);

// ─── Eval Server (persistent daemon for SWE-bench) ─────────────────

program
  .command('eval-server')
  .description('Start lightweight HTTP server for fast tool calls during evaluation')
  .option('-p, --port <port>', 'Port number', '4848')
  .option('--idle-timeout <seconds>', 'Auto-shutdown after N seconds idle (0 = disabled)', '0')
  .action(evalServerCommand);

// ─── Memory Commands ─────────────────────────────────────────────

program
  .command('memory')
  .description('List recent observations or search memory')
  .option('-s, --search <text>', 'Search observations')
  .option('-p, --project <name>', 'Filter by project')
  .action(memoryCommand);

program
  .command('note <title>')
  .description('Quick-add an observation')
  .option('-t, --type <type>', 'Observation type (learning, preference, do, dont, decision, bug, pattern, note)', 'note')
  .option('-s, --scope <scope>', 'Scope: repo or global', 'repo')
  .option('--tags <tags>', 'Comma-separated tags')
  .option('-c, --content <text>', 'Detailed content (defaults to title)')
  .action(noteCommand);

program.command('learnings [project]').description('List learnings').action(learningsCommand);
program.command('dos [project]').description('List "do" rules').action(dosCommand);
program.command('donts [project]').description('List "dont" rules').action(dontsCommand);
program.command('preferences [project]').description('List preferences').action(preferencesCommand);
program.command('decisions [project]').description('List architecture decisions').action(decisionsCommand);
program.command('bugs [project]').description('List known bugs + resolutions').action(bugsCommand);
program.command('patterns [project]').description('List recurring patterns').action(patternsCommand);

program
  .command('app')
  .description('Launch the CodeIndex desktop app')
  .action(async () => {
    const { spawn } = await import('node:child_process');
    const pathMod = await import('path');
    const fsp = await import('fs/promises');

    const cliDir = pathMod.default.dirname(new URL(import.meta.url).pathname);
    const appLocations = [
      '/Applications/CodeIndex.app',
      `${process.env.HOME}/Applications/CodeIndex.app`,
      pathMod.default.join(cliDir, '../../codeindex-web/build/CodeIndex.app'),
    ];

    for (const appPath of appLocations) {
      try {
        await fsp.default.access(appPath);
        console.log(`  Launching CodeIndex Desktop...`);
        spawn('open', [appPath], { detached: true, stdio: 'ignore' });
        process.exit(0);
      } catch {
        continue;
      }
    }

    // Fallback: open in browser via serve
    console.log('  Desktop app not found. Starting server with browser...');
    const { createServer } = await import('../server/api.js');
    await createServer(4747);
    const { exec } = await import('node:child_process');
    exec('open http://localhost:4747');
  });

// Bare `codeindex` with no subcommand: auto-update if in an indexed repo
const parsed = program.parseOptions(process.argv.slice(2));
const userArgs = parsed.operands;
if (userArgs.length === 0 && !process.argv.slice(2).some(a => ['-V', '--version', '-h', '--help'].includes(a))) {
  const { findRegistryEntry } = await import('../storage/repo-manager.js');
  const { isGitRepo } = await import('../storage/git.js');
  if (isGitRepo(process.cwd())) {
    const entry = await findRegistryEntry(process.cwd());
    if (entry) {
      await analyzeCommand();
    } else {
      // Git repo but not indexed — show banner, then prompt for project name
      printBanner();
      const { deriveProjectName } = await import('../storage/git.js');
      const { promptProjectName } = await import('./prompt-utils.js');
      const defaultName = deriveProjectName(process.cwd());
      const projectName = await promptProjectName(defaultName);
      if (projectName) {
        await analyzeCommand(projectName, undefined, { skipBanner: true });
      } else {
        console.log('  Run "codeindex" again when you want to index this project.');
      }
    }
  } else {
    // Not a git repo — show banner + help
    program.parse([...process.argv, '--help']);
  }
} else {
  program.parse(process.argv);
}
