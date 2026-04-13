import { existsSync } from "fs";
import { join, resolve } from "path";
import { createLogger, LogCategory } from "../lib/logger";

const log = createLogger('ServerManager', LogCategory.API, 'BN');
const serverLog = createLogger('API', LogCategory.API, 'BN');

/**
 * Manages the Node.js subprocess running the CodeIndex API server.
 * Handles startup, health monitoring, and clean shutdown.
 */
export class ServerManager {
  private proc: ReturnType<typeof Bun.spawn> | null = null;
  public port: number;
  public isRunning = false;

  constructor(port: number) {
    this.port = port;
  }

  async start(): Promise<void> {
    // Check if a server is already running on this port
    if (await this.healthCheck()) {
      log.info(`Server already running on port ${this.port}, reusing it`);
      this.isRunning = true;
      return;
    }

    const serverPath = this.findServerPath();
    log.info(`Starting server from: ${serverPath}`);

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
              serverLog.info(line);
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
        this.isRunning = false;
        if (code !== 0 && !this.isRunning) {
          clearTimeout(timeout);
          reject(new Error(`Server exited with code ${code}`));
        }
      });
    });
  }

  stop(): void {
    if (this.proc) {
      log.info("Stopping server...");
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

  private findServerPath(): string {
    const serverRelative = join("codeindex", "dist", "cli", "index.js");
    log.debug(`Looking for: ${serverRelative}`);
    log.debug(`CWD: ${process.cwd()}`);
    log.debug(`CODEINDEX_ROOT: ${Bun.env["CODEINDEX_ROOT"] ?? "(not set)"}`);

    // 1. CODEINDEX_ROOT env var (set by root package.json scripts)
    const root = Bun.env["CODEINDEX_ROOT"];
    if (root) {
      const candidate = join(root, serverRelative);
      log.debug(`Checking CODEINDEX_ROOT: ${candidate}`);
      if (existsSync(candidate)) {
        log.debug("Found server via CODEINDEX_ROOT");
        return candidate;
      }
    }

    // 2. Walk up from cwd to find monorepo root containing codeindex/dist/
    let dir = process.cwd();
    for (let i = 0; i < 10; i++) {
      const candidate = join(dir, serverRelative);
      log.debug(`Walk-up ${i}: ${candidate} exists=${existsSync(candidate)}`);
      if (existsSync(candidate)) {
        return candidate;
      }
      const parent = resolve(dir, "..");
      if (parent === dir) break;
      dir = parent;
    }

    log.error("FAILED: Could not find codeindex server anywhere!");
    return "codeindex";
  }
}
