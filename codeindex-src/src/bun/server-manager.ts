import { existsSync } from "fs";
import { join, resolve } from "path";
import { createLogger, LogCategory } from "../lib/logger";

const log = createLogger('Server', LogCategory.API, 'BN');

/**
 * Manages the codeindex API server subprocess.
 * Starts `node codeindex/dist/cli/index.js serve` and monitors health.
 */
export class ServerManager {
  private proc: ReturnType<typeof Bun.spawn> | null = null;
  public port: number;
  public isRunning = false;

  constructor(port: number) {
    this.port = port;
  }

  async start(): Promise<void> {
    if (await this.healthCheck()) {
      log.info(`Port ${this.port} in use, killing old process...`);
      await this.killExistingProcess();
      await Bun.sleep(500);
    }

    const serverPath = this.findServerPath();
    log.info(`Starting: ${serverPath}`);

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error("Server startup timeout (15s)"));
      }, 15000);

      this.proc = Bun.spawn(
        ["node", "--max-old-space-size=8192", serverPath, "serve", "--port", String(this.port)],
        {
          stdio: ["ignore", "pipe", "pipe"],
          env: {
            ...process.env,
            NODE_ENV: "production",
            CODEINDEX_DESKTOP: "1",
          },
        }
      );

      const decoder = new TextDecoder();
      const readStream = async (stream: ReadableStream<Uint8Array>) => {
        const reader = stream.getReader();
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const text = decoder.decode(value);
            for (const line of text.split("\n").filter(Boolean)) {
              log.info(line);
              if (!this.isRunning && (line.includes("running on") || line.includes("listening"))) {
                this.isRunning = true;
                clearTimeout(timeout);
                resolve();
              }
            }
          }
        } catch {
          // Stream ended
        }
      };

      readStream(this.proc.stdout as ReadableStream<Uint8Array>);
      readStream(this.proc.stderr as ReadableStream<Uint8Array>);

      this.proc.exited.then((code) => {
        if (!this.isRunning) {
          clearTimeout(timeout);
          reject(new Error(`Server exited with code ${code}`));
        }
        this.isRunning = false;
      });
    });
  }

  stop(): void {
    if (this.proc) {
      log.info("Stopping...");
      this.proc.kill();
      this.proc = null;
      this.isRunning = false;
    }
  }

  async healthCheck(): Promise<boolean> {
    try {
      const res = await fetch(`http://localhost:${this.port}/api/repos`, {
        signal: AbortSignal.timeout(3000),
      });
      return res.ok;
    } catch {
      return false;
    }
  }

  private async killExistingProcess(): Promise<void> {
    try {
      const result = Bun.spawnSync(["lsof", "-ti", `:${this.port}`]);
      const pids = new TextDecoder().decode(result.stdout).trim().split("\n").filter(Boolean);
      for (const pid of pids) {
        const p = parseInt(pid);
        if (p > 0 && p !== process.pid) {
          log.info(`Killing PID ${p} on port ${this.port}`);
          process.kill(p, "SIGTERM");
        }
      }
    } catch {
      // lsof may not find anything — that's fine
    }
  }

  private findServerPath(): string {
    const target = join("codeindex", "dist", "cli", "index.js");

    // 1. CODEINDEX_ROOT env var
    const root = Bun.env["CODEINDEX_ROOT"];
    if (root) {
      const candidate = join(root, target);
      if (existsSync(candidate)) return candidate;
    }

    // 2. Walk up from cwd
    let dir = process.cwd();
    for (let i = 0; i < 10; i++) {
      const candidate = join(dir, target);
      if (existsSync(candidate)) return candidate;
      const parent = resolve(dir, "..");
      if (parent === dir) break;
      dir = parent;
    }

    throw new Error(`Could not find codeindex server (looked for ${target})`);
  }
}
