#!/usr/bin/env node
/**
 * One-time setup: exports the persistent Brave/Chromium MCP profile to storageState.json.
 *
 * Run this:
 *   - After first-time setup (creates an empty storageState.json)
 *   - After migrating from persistent-profile to isolated mode (copies existing cookies)
 *   - After your login session expires and you need to re-export
 *
 * Usage:
 *   node scripts/export-mcp-session.mjs
 *
 * Requires: npm install -g playwright  (or use the globally installed playwright-mcp's playwright)
 */
import { chromium } from 'playwright';
import { readFileSync, existsSync, mkdirSync, writeFileSync } from 'fs';
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import os from 'os';

const __dirname = dirname(fileURLToPath(import.meta.url));
const configPath = resolve(__dirname, '..', '.playwright-mcp', 'config.json');

// --- Read config.json ---
if (!existsSync(configPath)) {
  console.error('ERROR: .playwright-mcp/config.json not found.');
  console.error('Copy .playwright-mcp/config.example.json to config.json and fill in your paths.');
  process.exit(1);
}

const config = JSON.parse(readFileSync(configPath, 'utf-8'));
const executablePath = config.executablePath;
const storageStatePath = (config.contextOptions?.storageState || '')
  .replace('~', os.homedir())
  .replace(/\\/g, '/');

if (!storageStatePath) {
  console.error('ERROR: config.json missing contextOptions.storageState path.');
  console.error('Make sure your config.json uses isolated mode with a storageState path.');
  process.exit(1);
}

if (!executablePath || !existsSync(executablePath)) {
  console.error(`ERROR: Browser executable not found: ${executablePath}`);
  console.error('Set executablePath in .playwright-mcp/config.json to your Brave/Chrome path.');
  process.exit(1);
}

// --- Resolve source profile dir (old persistent profile location) ---
const defaultProfileDir = join(os.homedir(), '.claude', 'mcp-brave-profile').replace(/\\/g, '/');
const sourceProfileDir = (config.userDataDir || defaultProfileDir)
  .replace('~', os.homedir())
  .replace(/\\/g, '/');

const hasExistingProfile = existsSync(sourceProfileDir);

console.log('Playwright MCP — session export');
console.log('================================');
console.log(`Executable:   ${executablePath}`);
console.log(`Source:       ${sourceProfileDir} ${hasExistingProfile ? '(found)' : '(not found — will create empty state)'}`);
console.log(`Output:       ${storageStatePath}`);
console.log('');

// Ensure output directory exists
const outputDir = dirname(storageStatePath);
if (!existsSync(outputDir)) {
  mkdirSync(outputDir, { recursive: true });
}

if (!hasExistingProfile) {
  // No existing profile — create empty storageState so Playwright MCP can start
  writeFileSync(storageStatePath, JSON.stringify({ cookies: [], origins: [] }, null, 2));
  console.log('Created empty storageState.json (no existing profile found).');
  console.log('');
  console.log('Next: restart Claude Code, then log in via the browser in your first test session.');
  console.log('After logging in, re-run this script to save the session for future sessions.');
  process.exit(0);
}

// Export from existing persistent profile
console.log('Launching headless browser to read profile cookies...');
let browser;
try {
  browser = await chromium.launchPersistentContext(sourceProfileDir, {
    executablePath,
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });

  await browser.storageState({ path: storageStatePath });
  console.log('');
  console.log(`storageState.json exported to: ${storageStatePath}`);
  console.log('');
  console.log('Done. Restart Claude Code to apply isolated mode with your saved session.');
} catch (err) {
  console.error('');
  console.error('Export failed:', err.message);
  console.error('');
  console.error('If the profile is locked (Brave still open), close all Brave windows first.');
  console.error('Or delete the lock file:');
  console.error(`  del "${sourceProfileDir}\\SingletonLock"   (Windows)`);
  console.error(`  rm "${sourceProfileDir}/SingletonLock"    (Mac/Linux)`);
  process.exit(1);
} finally {
  await browser?.close();
}
