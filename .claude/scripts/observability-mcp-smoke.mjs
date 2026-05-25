#!/usr/bin/env node
/**
 * Smoke-test the production observability MCP launchers.
 *
 * This is intentionally a real MCP stdio handshake, not a curl shortcut. Run it
 * before production debugging if an agent cannot query logs/metrics.
 *
 * By default VictoriaLogs opens its managed SSH tunnel. For local tunnel
 * debugging, set OBS_MCP_SMOKE_LOCAL_VICTORIALOGS=1.
 */
import { spawn } from 'child_process';

const ROOT = new URL('../..', import.meta.url).pathname;

function isoMinutesAgo(minutes) {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

function send(child, message) {
  child.stdin.write(`${JSON.stringify(message)}\n`);
}

function runMcpSmoke({ name, command, args, env, toolName, toolArguments, validate }) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: ROOT,
      env: { ...process.env, ...env },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    let settled = false;

    const fail = (message) => {
      if (settled) {
        return;
      }
      settled = true;
      child.kill('SIGTERM');
      reject(new Error(`${name}: ${message}\nSTDERR:\n${stderr.trim()}\nSTDOUT:\n${stdout.trim()}`));
    };

    const pass = () => {
      if (settled) {
        return;
      }
      settled = true;
      child.kill('SIGTERM');
      resolve();
    };

    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });

    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString();
      for (const line of stdout.split('\n')) {
        if (!line.startsWith('{')) {
          continue;
        }
        let payload;
        try {
          payload = JSON.parse(line);
        } catch {
          continue;
        }
        if (payload.id === 2) {
          if (payload.error) {
            fail(`tool call failed: ${JSON.stringify(payload.error)}`);
            return;
          }
          try {
            validate(payload.result);
            pass();
          } catch (err) {
            fail(err instanceof Error ? err.message : String(err));
          }
        }
      }
    });

    child.on('error', (err) => fail(err.message));
    child.on('exit', (code, signal) => {
      if (!settled) {
        fail(`exited before smoke completed: code=${code ?? 'null'} signal=${signal ?? 'null'}`);
      }
    });

    send(child, {
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: {
        protocolVersion: '2024-11-05',
        capabilities: {},
        clientInfo: { name: 'klai-observability-smoke', version: '1' },
      },
    });
    send(child, { jsonrpc: '2.0', method: 'notifications/initialized', params: {} });
    send(child, {
      jsonrpc: '2.0',
      id: 2,
      method: 'tools/call',
      params: { name: toolName, arguments: toolArguments },
    });

    setTimeout(() => fail('timed out after 20s'), 20_000).unref();
  });
}

const end = new Date().toISOString();
const start = isoMinutesAgo(120);

const localVictoriaLogs = process.env.OBS_MCP_SMOKE_LOCAL_VICTORIALOGS === '1';

await runMcpSmoke({
  name: 'VictoriaLogs MCP',
  command: 'node',
  args: ['.claude/scripts/victorialogs-launcher.mjs'],
  env: localVictoriaLogs
    ? {
        VICTORIALOGS_MCP_MANAGED_TUNNEL: '0',
        VL_INSTANCE_ENTRYPOINT: process.env.VL_INSTANCE_ENTRYPOINT || 'http://localhost:9428',
      }
    : {},
  toolName: 'query',
  toolArguments: {
    query: 'service:retrieval-api',
    start,
    end,
    limit: 1,
    timeout: '5s',
  },
  validate(result) {
    const text = JSON.stringify(result);
    if (text.includes('401') || text.includes('Unauthorized')) {
      throw new Error('VictoriaLogs returned unauthorized');
    }
  },
});
console.log('VictoriaLogs MCP smoke passed');

await runMcpSmoke({
  name: 'Grafana MCP',
  command: 'node',
  args: ['.claude/scripts/grafana-launcher.mjs'],
  env: {},
  toolName: 'list_datasources',
  toolArguments: { type: 'prometheus', limit: 3 },
  validate(result) {
    const text = JSON.stringify(result);
    if (text.includes('401') || text.includes('Unauthorized')) {
      throw new Error('Grafana returned unauthorized');
    }
    if (!text.includes('VictoriaMetrics')) {
      throw new Error('Grafana response did not include the VictoriaMetrics datasource');
    }
  },
});
console.log('Grafana MCP smoke passed');
