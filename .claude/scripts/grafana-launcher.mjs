#!/usr/bin/env node
/**
 * Grafana MCP launcher.
 *
 * mcp-grafana expects GRAFANA_API_KEY, while Klai developer shells expose the
 * read-only service-account token as GRAFANA_SERVICE_ACCOUNT_TOKEN. Keep that
 * mapping in one place so .mcp.json stays declarative and every agent session
 * starts Grafana with the same authenticated configuration.
 */
import { execFileSync, spawn } from 'child_process';
import { platform } from 'os';

const isWin = platform() === 'win32';

function readLoginShellEnv(name) {
  if (isWin) {
    return '';
  }

  const shell = process.env.SHELL || 'zsh';
  try {
    return execFileSync(shell, ['-lc', `printf %s "$${name}"`], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 3000,
    }).trim();
  } catch {
    return '';
  }
}

function firstEnv(...names) {
  for (const name of names) {
    const value = (process.env[name] || readLoginShellEnv(name)).trim();
    if (value) {
      return value;
    }
  }
  return '';
}

const env = { ...process.env };
env.GRAFANA_URL = env.GRAFANA_URL || 'https://grafana.getklai.com';
env.GRAFANA_API_KEY = firstEnv('GRAFANA_API_KEY', 'GRAFANA_SERVICE_ACCOUNT_TOKEN');

if (!env.GRAFANA_API_KEY) {
  process.stderr.write(
    'grafana-launcher error: set GRAFANA_SERVICE_ACCOUNT_TOKEN or GRAFANA_API_KEY in your shell profile.\n',
  );
  process.exit(1);
}

const child = spawn('uvx', ['mcp-grafana', '--disable-write'], {
  stdio: 'inherit',
  env,
  shell: isWin,
});

child.on('exit', (code, signal) => {
  if (signal) {
    process.exit(signal === 'SIGINT' ? 130 : 143);
    return;
  }
  process.exit(code ?? 0);
});

child.on('error', (err) => {
  process.stderr.write(`grafana-launcher error: ${err.message}\n`);
  process.exit(1);
});
