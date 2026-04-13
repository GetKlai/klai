import { createLogger, LogCategory } from '../lib/logger';

const log = createLogger('ViteManager', LogCategory.VITE, 'BN');
const viteLog = createLogger('Vite', LogCategory.VITE, 'JS');

/**
 * Manages the Vite dev server subprocess.
 * Handles startup, output routing through unified logger, and clean shutdown.
 */
export class ViteManager {
  private proc: ReturnType<typeof Bun.spawn> | null = null;
  public port: number;
  public isRunning = false;

  constructor(port: number) {
    this.port = port;
  }

  async start(webDir: string): Promise<void> {
    log.info(`Starting dev server from: ${webDir}`);

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Vite startup timeout (10s)')), 10000);

      this.proc = Bun.spawn(
        ['npx', 'vite', '--port', String(this.port), '--strictPort'],
        {
          cwd: webDir,
          stdio: ['ignore', 'pipe', 'pipe'],
          env: { ...process.env, FORCE_COLOR: '0' },  // Disable Vite's own colors
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
            for (const line of text.split('\n').filter(Boolean)) {
              // Strip ANSI codes from Vite output before re-logging
              const clean = line.replace(/\x1b\[[0-9;]*m/g, '').trim();
              if (!clean) continue;

              // Detect ready signal
              if (clean.includes('Local:') && clean.includes(String(this.port))) {
                if (!this.isRunning) {
                  this.isRunning = true;
                  clearTimeout(timeout);
                  resolve();
                }
              }

              // Route through unified logger
              if (clean.includes('error') || clean.includes('ERROR')) {
                viteLog.error(clean);
              } else if (clean.includes('warn') || clean.includes('WARN')) {
                viteLog.warn(clean);
              } else if (clean.includes('hmr update') || clean.includes('page reload')) {
                viteLog.debug(clean);  // HMR updates are debug-level
              } else {
                viteLog.info(clean);
              }
            }
          }
        } catch { /* stream ended */ }
      };

      readStream(this.proc.stdout as ReadableStream<Uint8Array>);
      readStream(this.proc.stderr as ReadableStream<Uint8Array>);

      this.proc.exited.then((code) => {
        this.isRunning = false;
        if (code !== 0) {
          clearTimeout(timeout);
          log.error(`Vite exited with code ${code}`);
        }
      });
    });
  }

  stop(): void {
    if (this.proc) {
      log.info('Stopping Vite dev server...');
      this.proc.kill();
      this.proc = null;
      this.isRunning = false;
    }
  }
}
