#!/usr/bin/env node
/**
 * Cross-platform VictoriaLogs MCP launcher.
 *
 * The MCP binary talks HTTP to VictoriaLogs. In production VictoriaLogs is only
 * reachable from core-01, so this launcher owns a short-lived SSH tunnel per
 * MCP process. That avoids the old shared localhost:9428 tunnel, which was
 * fragile when multiple Conductor/Claude sessions ran in parallel.
 *
 * Env vars:
 *   VICTORIALOGS_BASIC_AUTH_B64       Basic auth value, base64 user:password.
 *   VICTORIALOGS_MCP_MANAGED_TUNNEL   Set to 0 to use VL_INSTANCE_ENTRYPOINT as-is.
 *   VICTORIALOGS_SSH_HOST             SSH host, default core-01.
 *   VICTORIALOGS_CONTAINER            Container name, default klai-core-victorialogs-1.
 *   VICTORIALOGS_REMOTE_PORT          Remote container port, default 9428.
 *   VL_INSTANCE_ENTRYPOINT            Custom endpoint for mcp-victorialogs.
 */
import { execFileSync, spawn } from 'child_process';
import http from 'http';
import net from 'net';
import { homedir, platform } from 'os';
import { join } from 'path';

const isWin = platform() === 'win32';
const binName = isWin ? 'mcp-victorialogs.exe' : 'mcp-victorialogs';
const binPath = join(homedir(), 'bin', binName);

const DEFAULT_LOCAL_ENDPOINTS = new Set([
  '',
  'http://localhost:9428',
  'http://127.0.0.1:9428',
]);

function log(message) {
  process.stderr.write(`[victorialogs-launcher] ${message}\n`);
}

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

function configureAuth(env) {
  const auth = (env.VICTORIALOGS_BASIC_AUTH_B64 || readLoginShellEnv('VICTORIALOGS_BASIC_AUTH_B64')).trim();
  if (auth) {
    env.VICTORIALOGS_BASIC_AUTH_B64 = auth;
    env.VL_INSTANCE_HEADERS = `Authorization=Basic ${auth}`;
    return auth;
  }

  if (env.VL_INSTANCE_HEADERS?.includes('${')) {
    delete env.VL_INSTANCE_HEADERS;
  }
  return '';
}

function shouldUseManagedTunnel(env) {
  const flag = env.VICTORIALOGS_MCP_MANAGED_TUNNEL;
  if (flag != null && ['0', 'false', 'no'].includes(flag.toLowerCase())) {
    return false;
  }
  return DEFAULT_LOCAL_ENDPOINTS.has((env.VL_INSTANCE_ENTRYPOINT || '').replace(/\/$/, ''));
}

function assertSafeContainerName(name) {
  if (!/^[A-Za-z0-9_.-]+$/.test(name)) {
    throw new Error(`Unsafe VICTORIALOGS_CONTAINER value: ${name}`);
  }
}

function resolveContainerIp(env) {
  const sshHost = env.VICTORIALOGS_SSH_HOST || 'core-01';
  const container = env.VICTORIALOGS_CONTAINER || 'klai-core-victorialogs-1';
  assertSafeContainerName(container);

  const remoteCommand = [
    'docker',
    'inspect',
    '-f',
    "'{{range .NetworkSettings.Networks}}{{println .IPAddress}}{{end}}'",
    container,
  ].join(' ');

  const raw = execFileSync('ssh', [sshHost, remoteCommand], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    timeout: 10000,
  });
  const ip = raw
    .split(/\s+/)
    .map((part) => part.trim())
    .find(Boolean);
  if (!ip) {
    throw new Error(`Could not resolve IP for ${container} via ${sshHost}`);
  }
  return { ip, sshHost, container };
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close((err) => {
        if (err) {
          reject(err);
          return;
        }
        resolve(port);
      });
    });
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function healthCheck(port, auth) {
  return new Promise((resolve) => {
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        path: '/health',
        method: 'GET',
        timeout: 1000,
        headers: auth ? { Authorization: `Basic ${auth}` } : {},
      },
      (res) => {
        res.resume();
        resolve(res.statusCode != null && res.statusCode >= 200 && res.statusCode < 400);
      },
    );
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.on('error', () => resolve(false));
    req.end();
  });
}

async function startManagedTunnel(env, auth) {
  if (!auth) {
    throw new Error('VICTORIALOGS_BASIC_AUTH_B64 is required for VictoriaLogs MCP');
  }

  const localPort = await findFreePort();
  const remotePort = env.VICTORIALOGS_REMOTE_PORT || '9428';
  const { ip, sshHost, container } = resolveContainerIp(env);

  log(`starting session tunnel 127.0.0.1:${localPort} -> ${sshHost} -> ${container}:${remotePort}`);
  const tunnel = spawn(
    'ssh',
    [
      '-N',
      '-L',
      `127.0.0.1:${localPort}:${ip}:${remotePort}`,
      '-o',
      'ServerAliveInterval=30',
      '-o',
      'ServerAliveCountMax=3',
      '-o',
      'ExitOnForwardFailure=yes',
      sshHost,
    ],
    {
      stdio: ['ignore', 'ignore', 'inherit'],
    },
  );

  let tunnelExit = null;
  tunnel.on('exit', (code, signal) => {
    tunnelExit = { code, signal };
  });

  for (let attempt = 0; attempt < 20; attempt++) {
    if (tunnelExit) {
      throw new Error(`SSH tunnel exited before it became healthy: ${JSON.stringify(tunnelExit)}`);
    }
    if (await healthCheck(localPort, auth)) {
      return {
        process: tunnel,
        entrypoint: `http://127.0.0.1:${localPort}`,
      };
    }
    await sleep(250);
  }

  tunnel.kill('SIGTERM');
  throw new Error(`VictoriaLogs tunnel did not become healthy on port ${localPort}`);
}

async function run() {
  const env = { ...process.env };
  const auth = configureAuth(env);
  let tunnel = null;

  if (shouldUseManagedTunnel(env)) {
    tunnel = await startManagedTunnel(env, auth);
    env.VL_INSTANCE_ENTRYPOINT = tunnel.entrypoint;
  } else if (!env.VL_INSTANCE_ENTRYPOINT) {
    env.VL_INSTANCE_ENTRYPOINT = 'http://localhost:9428';
  }

  const child = spawn(binPath, [], {
    stdio: 'inherit',
    env,
  });

  let shuttingDown = false;
  const stopTunnel = () => {
    if (tunnel?.process && tunnel.process.exitCode == null) {
      tunnel.process.kill('SIGTERM');
    }
  };

  const shutdown = (signal) => {
    shuttingDown = true;
    child.kill(signal);
    stopTunnel();
  };

  for (const signal of ['SIGINT', 'SIGTERM']) {
    process.on(signal, () => shutdown(signal));
  }

  child.on('exit', (code, signal) => {
    shuttingDown = true;
    stopTunnel();
    if (signal) {
      process.exit(signal === 'SIGINT' ? 130 : 143);
      return;
    }
    process.exit(code ?? 0);
  });

  child.on('error', (err) => {
    shuttingDown = true;
    stopTunnel();
    process.stderr.write(`victorialogs-launcher error: ${err.message}\n`);
    process.stderr.write(`Expected binary at: ${binPath}\n`);
    process.exit(1);
  });

  tunnel?.process.on('exit', (code, signal) => {
    if (shuttingDown) {
      return;
    }
    log(`session tunnel exited unexpectedly: code=${code ?? 'null'} signal=${signal ?? 'null'}`);
    child.kill('SIGTERM');
    process.exit(1);
  });
}

run().catch((err) => {
  process.stderr.write(`victorialogs-launcher error: ${err.message}\n`);
  process.stderr.write(`Expected binary at: ${binPath}\n`);
  process.exit(1);
});
