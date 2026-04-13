import {
  BrowserWindow,
  BrowserView,
  Updater,
  Utils,
  type RPCSchema,
} from "electrobun/bun";
import { existsSync } from "fs";
import { join, resolve } from "path";
import { ServerManager } from "./server-manager";
import { ViteManager } from "./vite-manager";
import { createLogger, LogCategory, initFileLogging, patchConsole } from "../lib/logger";

// Patch console FIRST — all console.log/warn/error calls get unified formatting
patchConsole('BN');

const log = createLogger('Boot', LogCategory.BOOT, 'BN');

// ── Project Root ────────────────────────────────────────────────────

function findProjectRoot(): string {
  const root = Bun.env["CODEINDEX_ROOT"];
  if (root && existsSync(join(root, "codeindex-web"))) return root;

  let dir = process.cwd();
  for (let i = 0; i < 10; i++) {
    if (existsSync(join(dir, "codeindex-web", "package.json"))) return dir;
    const parent = resolve(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return process.cwd();
}

// ── Ports ───────────────────────────────────────────────────────────

const API_PORT = 4747;
const VITE_PORT = 4848;

// ── Managers ────────────────────────────────────────────────────────

const serverManager = new ServerManager(API_PORT);
const viteManager = new ViteManager(VITE_PORT);

// ── RPC Schema ──────────────────────────────────────────────────────

export type CodeIndexRPC = {
  bun: RPCSchema<{
    requests: {
      openInEditor: {
        params: { path: string; line?: number; editor?: string };
        response: { success: boolean; error?: string };
      };
      selectDirectory: {
        params: {};
        response: { path: string | null };
      };
      getSystemInfo: {
        params: {};
        response: {
          platform: string;
          homedir: string;
          codeindexDir: string;
          serverPort: number;
        };
      };
      quit: {
        params: {};
        response: { success: boolean };
      };
    };
    messages: {};
  }>;
  webview: RPCSchema<{
    requests: {};
    messages: {
      serverReady: { port: number; url: string };
      serverError: { message: string };
    };
  }>;
};

// ── RPC Handlers ────────────────────────────────────────────────────

const rpc = BrowserView.defineRPC<CodeIndexRPC>({
  maxRequestTime: 10000,
  handlers: {
    requests: {
      openInEditor: async ({ path, line, editor }) => {
        try {
          const editorCmd = editor || "cursor";
          const target = line ? `${path}:${line}` : path;
          Bun.spawn([editorCmd, target], {
            stdio: ["ignore", "ignore", "ignore"],
          });
          return { success: true };
        } catch (e) {
          return { success: false, error: (e as Error).message };
        }
      },

      selectDirectory: async () => {
        try {
          const paths = await Utils.openFileDialog({
            startingFolder: Bun.env["HOME"] || "/",
            canChooseFiles: false,
            canChooseDirectory: true,
            allowsMultipleSelection: false,
          });
          return { path: paths[0] && paths[0] !== "" ? paths[0] : null };
        } catch {
          return { path: null };
        }
      },

      getSystemInfo: async () => ({
        platform: process.platform,
        homedir: Bun.env["HOME"] || "/",
        codeindexDir: `${Bun.env["HOME"]}/.codeindex`,
        serverPort: serverManager.port,
      }),

      quit: async () => {
        shutdown();
        return { success: true };
      },
    },
    messages: {},
  },
});

// ── Shutdown ────────────────────────────────────────────────────────

function shutdown(): void {
  viteManager.stop();
  serverManager.stop();
  setTimeout(() => process.exit(0), 100);
}

// ── Boot ────────────────────────────────────────────────────────────

async function boot(): Promise<void> {
  // Initialize file logging in project root
  const projectRoot = findProjectRoot();
  const logFile = join(projectRoot, "codeindex.log");
  initFileLogging(logFile);

  log.info("CodeIndex Desktop v1.0.0");
  log.info(`CWD: ${process.cwd()}`);
  log.info(`Log file: ${logFile}`);

  const channel = await Updater.localInfo.channel();
  const isDev = channel === "dev";

  // Start API server
  try {
    await serverManager.start();
    log.info(`API server ready on port ${API_PORT}`);
  } catch (err) {
    log.error(`API server failed: ${(err as Error).message}`);
  }

  // Determine URL for the webview
  let url: string;

  if (isDev) {
    // Dev mode: start Vite automatically
    try {
      await viteManager.start();
      url = `http://localhost:${VITE_PORT}`;
      log.info(`Vite dev server ready on port ${VITE_PORT}`);
    } catch (err) {
      log.warn(`Vite failed, falling back to API server: ${(err as Error).message}`);
      url = `http://localhost:${API_PORT}`;
    }
  } else {
    // Production: static files served by the API server or bundled
    url = `http://localhost:${API_PORT}`;
  }

  // Open the window
  const mainWindow = new BrowserWindow({
    title: "CodeIndex",
    url,
    frame: { width: 1400, height: 900, x: 100, y: 100 },
    titleBarStyle: "hiddenInset",
    rpc,
  });

  if (serverManager.isRunning) {
    mainWindow.webview.rpc?.send.serverReady({
      port: API_PORT,
      url: `http://localhost:${API_PORT}`,
    });
  }

  mainWindow.on("close", () => shutdown());

  log.info("Ready!");
}

boot().catch((err) => {
  log.error(`Fatal: ${err}`);
  shutdown();
});
