import { join } from "path";
import { createLogger, LogCategory } from "../lib/logger";

const log = createLogger('Vite', LogCategory.VITE, 'JS');

/**
 * Manages the Vite dev server subprocess.
 * Starts `bunx vite` inside codeindex-web/ and waits until responding.
 */
export class ViteManager {
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

    const webDir = this.findWebDir();
    log.info(`Starting dev server from: ${webDir}`);

    this.proc = Bun.spawn(
      ["bunx", "vite", "--port", String(this.port), "--strictPort"],
      {
        cwd: webDir,
        stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, FORCE_COLOR: "1" },
      }
    );

    // Stream logs to console
    const pipe = (stream: ReadableStream<Uint8Array>) => {
      const reader = stream.getReader();
      const decoder = new TextDecoder();
      (async () => {
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            for (const line of decoder.decode(value).split("\n").filter(Boolean)) {
              log.info(line);
            }
          }
        } catch { /* stream ended */ }
      })();
    };
    pipe(this.proc.stdout as ReadableStream<Uint8Array>);
    pipe(this.proc.stderr as ReadableStream<Uint8Array>);

    // Poll health check — fast and reliable, no log parsing needed
    const started = Date.now();
    while (Date.now() - started < 30000) {
      await Bun.sleep(300);
      if (await this.healthCheck()) {
        this.isRunning = true;
        log.info(`Ready in ${Date.now() - started}ms`);
        return;
      }
    }

    throw new Error("Vite startup timeout (30s)");
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
      const res = await fetch(`http://localhost:${this.port}`, {
        method: "HEAD",
        signal: AbortSignal.timeout(1000),
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

  private findWebDir(): string {
    let dir = process.cwd();
    for (let i = 0; i < 10; i++) {
      const candidate = join(dir, "codeindex-web");
      if (Bun.file(join(candidate, "package.json")).size > 0) {
        return candidate;
      }
      const parent = join(dir, "..");
      if (parent === dir) break;
      dir = parent;
    }
    throw new Error("Could not find codeindex-web/ directory");
  }
}
