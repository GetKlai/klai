#!/usr/bin/env node
/**
 * Cross-platform Playwright MCP launcher.
 *
 * Pattern: `--isolated --storage-state ~/.claude/mcp-storageState.json`.
 * Each Claude Code session gets its own ephemeral Chrome profile that is
 * preloaded with login cookies + localStorage from the storage-state JSON.
 * Multiple parallel Claude Code sessions are safe (no profile lock) and
 * all start authenticated.
 *
 * Why this pattern (May 2026, post-research):
 *   - Microsoft has no officially-supported "parallel + shared login"
 *     CLI option besides this. Issue #1530 (named session management)
 *     was closed as "not planned".
 *   - The browser extension is the alternative Microsoft pushes; we
 *     reject it because it grants the AI full access to every other
 *     site the user is logged in to in their daily Chrome.
 *   - PR #346's `--user-data-dir` is single-instance, so it cannot
 *     serve parallel sessions.
 *   - The April 2026 attempt at this pattern (PR #354) failed because
 *     the seed step relied on `npx playwright codegen --save-storage`,
 *     which is flaky on macOS.
 *
 * The seed step (May 2026):
 *   In @playwright/mcp >= 0.0.67 there is an MCP tool
 *   `browser_storage_state` that, when invoked from a running session,
 *   writes the current cookies + localStorage to the storage-state
 *   file. No external codegen, no Inspector window, no Ctrl+C dance.
 *
 *   1. With this launcher in place but no storage-state file yet,
 *      open a Playwright MCP session — the browser starts logged out.
 *   2. `browser_navigate` to a login URL, log in by hand.
 *   3. Have the AI call `browser_storage_state` — file is written.
 *   4. Restart Claude Code (so the launcher picks the file up at
 *      startup via the `--storage-state` flag).
 *   5. Refresh the same way every ~3 weeks when Google's session
 *      cookies expire.
 *
 * Why CLI flags instead of a JSON config file:
 *   microsoft/playwright-mcp#1446 — `userDataDir` set in a JSON
 *   --config file is silently ignored on @playwright/mcp@0.0.70.
 *   CLI flag works correctly.
 *
 * Why `--browser chrome`:
 *   On @>=0.0.74 the valid `--browser` values are
 *   chrome|firefox|webkit|msedge. `chromium` was dropped. `chrome`
 *   uses the system Google Chrome installation but, combined with
 *   `--isolated`, gets its own ephemeral profile per session — no
 *   collision with the user's regular Chrome browser.
 */
import { spawn } from 'child_process';
import { homedir, platform } from 'os';
import { join } from 'path';
import { existsSync } from 'fs';

const isWin = platform() === 'win32';
const STATE_FILE = join(homedir(), '.claude', 'mcp-storageState.json');

const args = [
  '--yes',
  '@playwright/mcp@latest',
  '--browser', 'chrome',
  '--isolated',
];

if (existsSync(STATE_FILE)) {
  args.push('--storage-state', STATE_FILE);
} else {
  process.stderr.write(
    `playwright-launcher: ${STATE_FILE} not found.\n` +
    `Browser starts logged-out. To seed the file from inside a running\n` +
    `MCP session: open a login URL via browser_navigate, log in by hand,\n` +
    `then have the AI call the browser_storage_state MCP tool. Restart\n` +
    `Claude Code afterwards so the launcher picks up the file.\n`
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
