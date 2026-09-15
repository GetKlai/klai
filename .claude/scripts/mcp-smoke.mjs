#!/usr/bin/env node
/**
 * Smoke-test every MCP server declared in the repo's .mcp.json.
 *
 * This is intentionally a real MCP stdio handshake — initialize, then
 * tools/list — because "the binary exists" has repeatedly been mistaken for
 * "the server is wired up". A server that starts but advertises no tools is a
 * failure here, which is what a broken Serena project path or a missing
 * launcher dependency actually looks like.
 *
 * The server list is read from .mcp.json rather than hard-coded, so a server
 * added there cannot silently escape the check.
 *
 * tools/list never queries anything upstream, and Playwright MCP does not
 * launch a browser until a tool is called, but the handshake does start the
 * real launcher. So grafana still needs its token and victorialogs still opens
 * its tunnel: for those two the credential IS the wiring being checked.
 *
 *   node .claude/scripts/mcp-smoke.mjs           # handshake every server
 *   node .claude/scripts/mcp-smoke.mjs --deep    # also run the observability
 *                                                # tool calls that prove the
 *                                                # production credentials work
 *   node .claude/scripts/mcp-smoke.mjs serena    # one server by name
 *
 * With --deep, set OBS_MCP_SMOKE_LOCAL_VICTORIALOGS=1 to debug against an
 * already-running manual tunnel instead of the launcher's managed one.
 */
import { spawn } from 'child_process';
import { readFileSync } from 'fs';

const ROOT = new URL('../..', import.meta.url).pathname;
const START_TIMEOUT_MS = 60_000;

const args = process.argv.slice(2);
const deep = args.includes('--deep');
const onlyNames = args.filter((arg) => !arg.startsWith('--'));
// A mistyped flag or server name must not quietly narrow the run to a subset
// that then reports success — that is the same silent-pass failure this script
// exists to catch.
const unknownFlags = args.filter((arg) => arg.startsWith('--') && arg !== '--deep');
if (unknownFlags.length > 0) {
  console.error(`Unknown option(s): ${unknownFlags.join(', ')}. Only --deep is supported.`);
  process.exit(2);
}

function isoMinutesAgo(minutes) {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

function send(child, message) {
  child.stdin.write(`${JSON.stringify(message)}\n`);
}

/**
 * Drive one MCP server over stdio and resolve once `check` accepts the reply
 * to request id 2. Rejects with the server's stderr attached, because a
 * launcher that refuses to start explains why on stderr and that explanation
 * is the whole diagnostic value of this script.
 */
function runMcp({ name, command, args: commandArgs, env, request, check, timeoutMs }) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, commandArgs, {
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

    const pass = (summary) => {
      if (settled) {
        return;
      }
      settled = true;
      child.kill('SIGTERM');
      resolve(summary);
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
        if (payload.id !== 2) {
          continue;
        }
        if (payload.error) {
          fail(`request failed: ${JSON.stringify(payload.error)}`);
          return;
        }
        // A tool that ran and failed reports isError on the result, not as a
        // JSON-RPC error, so a check that only inspects the text can read a
        // timeout or a 403 as a pass. Reject the whole result instead.
        if (payload.result?.isError) {
          fail(`tool reported an error: ${JSON.stringify(payload.result).slice(0, 500)}`);
          return;
        }
        try {
          pass(check(payload.result));
        } catch (err) {
          fail(err instanceof Error ? err.message : String(err));
        }
      }
    });

    child.on('error', (err) => fail(err.message));
    child.on('exit', (code, signal) => {
      if (!settled) {
        fail(`exited before the smoke completed: code=${code ?? 'null'} signal=${signal ?? 'null'}`);
      }
    });

    send(child, {
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: {
        protocolVersion: '2024-11-05',
        capabilities: {},
        clientInfo: { name: 'klai-mcp-smoke', version: '1' },
      },
    });
    send(child, { jsonrpc: '2.0', method: 'notifications/initialized', params: {} });
    send(child, { jsonrpc: '2.0', id: 2, ...request });

    setTimeout(() => fail(`timed out after ${timeoutMs / 1000}s`), timeoutMs).unref();
  });
}

const end = new Date().toISOString();
const start = isoMinutesAgo(120);
const localVictoriaLogs = process.env.OBS_MCP_SMOKE_LOCAL_VICTORIALOGS === '1';

/**
 * Tool calls that prove something the handshake cannot. `tier: 'default'` runs
 * always because the claim it proves needs no credentials; `tier: 'deep'` needs
 * production access and only runs under --deep.
 */
const toolChecks = {
  serena: {
    tier: 'default',
    env: {},
    request: { method: 'tools/call', params: { name: 'initial_instructions', arguments: {} } },
    check(result) {
      // Serena answering at all does not mean it bound to this worktree. The
      // project prompt only reaches the client when --project . resolved to
      // this repo's .serena/project.yml, so finding it here is the binding
      // proof — and it is what silently broke the last two times.
      if (!JSON.stringify(result).includes('Klai monorepo')) {
        throw new Error('initial_instructions did not carry .serena/project.yml — wrong project root');
      }
      return 'bound to this worktree (.serena/project.yml prompt present)';
    },
  },
  victorialogs: {
    tier: 'deep',
    env: localVictoriaLogs
      ? {
          VICTORIALOGS_MCP_MANAGED_TUNNEL: '0',
          VL_INSTANCE_ENTRYPOINT: process.env.VL_INSTANCE_ENTRYPOINT || 'http://localhost:9428',
        }
      : {},
    request: {
      method: 'tools/call',
      params: {
        name: 'query',
        arguments: { query: 'service:retrieval-api', start, end, limit: 1, timeout: '5s' },
      },
    },
    check(result) {
      const text = JSON.stringify(result);
      if (text.includes('401') || text.includes('Unauthorized')) {
        throw new Error('VictoriaLogs returned unauthorized');
      }
      return 'query returned authorized';
    },
  },
  grafana: {
    tier: 'deep',
    env: {},
    request: {
      method: 'tools/call',
      params: { name: 'list_datasources', arguments: { type: 'prometheus', limit: 3 } },
    },
    check(result) {
      const text = JSON.stringify(result);
      if (text.includes('401') || text.includes('Unauthorized')) {
        throw new Error('Grafana returned unauthorized');
      }
      if (!text.includes('VictoriaMetrics')) {
        throw new Error('Grafana response did not include the VictoriaMetrics datasource');
      }
      return 'list_datasources found VictoriaMetrics';
    },
  },
};

const config = JSON.parse(readFileSync(new URL('../../.mcp.json', import.meta.url), 'utf8'));
const servers = Object.entries(config.mcpServers).filter(
  ([name]) => onlyNames.length === 0 || onlyNames.includes(name),
);

const unknownNames = onlyNames.filter((name) => !(name in config.mcpServers));
if (unknownNames.length > 0) {
  console.error(
    `Not declared in .mcp.json: ${unknownNames.join(', ')}. ` +
      `Known servers: ${Object.keys(config.mcpServers).join(', ')}.`,
  );
  process.exit(2);
}

let failures = 0;

for (const [name, server] of servers) {
  const toolCheck = toolChecks[name];
  // The handshake starts the real launcher, so an override like
  // OBS_MCP_SMOKE_LOCAL_VICTORIALOGS has to apply here too — otherwise the
  // handshake opens the managed production tunnel the override exists to avoid.
  const env = { ...(server.env ?? {}), ...(toolCheck?.env ?? {}) };
  try {
    const tools = await runMcp({
      name,
      command: server.command,
      args: server.args ?? [],
      env,
      request: { method: 'tools/list', params: {} },
      check(result) {
        const count = result?.tools?.length ?? 0;
        if (count === 0) {
          throw new Error('handshake succeeded but the server advertised no tools');
        }
        return count;
      },
      timeoutMs: START_TIMEOUT_MS,
    });
    console.log(`PASS ${name}: handshake ok, ${tools} tools`);

    if (toolCheck && (deep || toolCheck.tier === 'default')) {
      const summary = await runMcp({
        name,
        command: server.command,
        args: server.args ?? [],
        env,
        request: toolCheck.request,
        check: toolCheck.check,
        timeoutMs: START_TIMEOUT_MS,
      });
      console.log(`PASS ${name} (${toolCheck.tier}): ${summary}`);
    }
  } catch (err) {
    failures += 1;
    console.error(`FAIL ${err instanceof Error ? err.message : String(err)}`);
  }
}

console.log(`\n${servers.length - failures}/${servers.length} MCP servers passed.`);
process.exit(failures === 0 ? 0 : 1);
