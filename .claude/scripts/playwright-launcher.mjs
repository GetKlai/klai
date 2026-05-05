#!/usr/bin/env node
/**
 * Cross-platform Playwright MCP launcher.
 *
 * Pattern: --isolated + --storage-state (canonical Playwright multi-session
 * authentication). Each Claude Code session gets its own ephemeral Chromium
 * profile that is preloaded with login cookies/localStorage from a shared
 * storageState file. So:
 *
 *   - Multiple sessions run side-by-side without profile-lock conflicts
 *   - All sessions start authenticated (Google, getklai, etc.)
 *   - Browser windows are visible (not headless)
 *   - No file-copy bookkeeping; storage-state is read-only at startup
 *
 * One-time login (run when storage-state is missing or expired, ~once every
 * few weeks for Google):
 *
 *   npx --yes playwright codegen \
 *     --save-storage="$HOME/.claude/mcp-storageState.json" \
 *     --browser=chromium https://voys.getklai.com
 *
 * Log in to all sites you need, then close the browser window. The file is
 * written automatically.
 *
 * Why CLI flags instead of a JSON config file:
 *   microsoft/playwright-mcp#1446 — `userDataDir` set in a JSON --config
 *   file is silently ignored on @playwright/mcp@0.0.70.
 *
 * Why bundled Chromium (no --executable-path):
 *   Brave/Chrome on Windows cannot run two simultaneous instances with
 *   different user profiles. Bundled Chromium has no such constraint.
 */
import { spawn } from 'child_process';
import { homedir, platform } from 'os';
import { join } from 'path';
import { existsSync } from 'fs';

const isWin = platform() === 'win32';
const STATE_FILE = join(homedir(), '.claude', 'mcp-storageState.json');

const args = [
  '--yes',
  '@playwright/mcp@0.0.70',
  '--browser', 'chromium',
  '--isolated',
];

if (existsSync(STATE_FILE)) {
  args.push('--storage-state', STATE_FILE);
} else {
  process.stderr.write(
    `playwright-launcher: ${STATE_FILE} not found.\n` +
    `Run once to generate it:\n` +
    `  npx --yes playwright codegen --save-storage="${STATE_FILE}" --browser=chromium https://voys.getklai.com\n` +
    `Browser will start without preloaded login state.\n`
  );
}

const child = spawn('npx', args, {
  stdio: 'inherit',
  shell: isWin, // Windows requires shell:true for npx to resolve
});

child.on('exit', (code) => process.exit(code ?? 0));
child.on('error', (err) => {
  process.stderr.write(`playwright-launcher error: ${err.message}\n`);
  process.exit(1);
});
