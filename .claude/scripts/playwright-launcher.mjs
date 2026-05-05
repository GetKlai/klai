#!/usr/bin/env node
/**
 * Cross-platform Playwright MCP launcher.
 *
 * Uses Playwright's bundled Chromium (NOT Brave/Chrome) with a persistent
 * userDataDir, so login state survives across Claude Code restarts.
 *
 * Why CLI flags instead of a JSON config file:
 *   microsoft/playwright-mcp#1446 — `userDataDir` set in a JSON --config file
 *   is silently ignored on @playwright/mcp@0.0.70; the browser falls back to
 *   an in-memory profile and login state is lost. Passing --user-data-dir
 *   directly on the command line works correctly.
 *
 * Single-instance limitation:
 *   Persistent profiles can only be opened by one browser at a time. A second
 *   Claude Code session that needs a browser should use the playwright-isolated
 *   MCP server (already wired in .mcp.json) instead.
 *
 * Profile location:
 *   ~/.claude/mcp-chromium-profile/ (cross-platform, derived from os.homedir())
 */
import { spawn } from 'child_process';
import { homedir, platform } from 'os';
import { join } from 'path';

const userDataDir = join(homedir(), '.claude', 'mcp-chromium-profile');

const child = spawn(
  'npx',
  [
    '--yes',
    '@playwright/mcp@0.0.70',
    '--browser', 'chromium',
    '--user-data-dir', userDataDir,
  ],
  {
    stdio: 'inherit',
    shell: platform() === 'win32', // Windows requires shell:true for npx to resolve
  }
);

child.on('exit', (code) => process.exit(code ?? 0));
child.on('error', (err) => {
  process.stderr.write(`playwright-launcher error: ${err.message}\n`);
  process.exit(1);
});
