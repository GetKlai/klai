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
        serverManager.stop();
        viteManager.stop();
        setTimeout(() => process.exit(0), 100);
        return { success: true };
      },
    },
    messages: {},
  },
});

// ── Configuration ───────────────────────────────────────────────────

const SERVER_PORT = 4747;
const VITE_PORT = 4848;
const serverManager = new ServerManager(SERVER_PORT);
const viteManager = new ViteManager(VITE_PORT);

// ── URL Resolution ──────────────────────────────────────────────────

async function getMainViewUrl(): Promise<string> {
  const channel = await Updater.localInfo.channel();

  if (channel === "dev") {
    if (viteManager.isRunning) {
      log.info(`HMR: Using Vite dev server at localhost:${VITE_PORT}`);
      return `http://localhost:${VITE_PORT}`;
    }

    // Vite not managed by us — check if one is already running
    try {
      await fetch(`http://localhost:${VITE_PORT}`, {
        method: "HEAD",
        signal: AbortSignal.timeout(1000),
      });
      log.info(`HMR: Found external Vite dev server at localhost:${VITE_PORT}`);
      return `http://localhost:${VITE_PORT}`;
    } catch {
      log.info("No Vite dev server available, using API server for static files");
    }
  }

  return `http://localhost:${SERVER_PORT}`;
}

// ── Boot ────────────────────────────────────────────────────────────

async function boot(): Promise<void> {
  // Initialize file logging in project root
  const projectRoot = findProjectRoot();
  const logFile = join(projectRoot, "codeindex.log");
  initFileLogging(logFile);

  log.info("CodeIndex Desktop v1.0.0");
  log.info(`Log file: ${logFile}`);

  // Start API server
  try {
    await serverManager.start();
    log.info(`API server ready on port ${SERVER_PORT}`);
  } catch (err) {
    log.error(`API server failed: ${(err as Error).message}`);
  }

  // Start Vite dev server in dev channel
  const channel = await Updater.localInfo.channel();
  if (channel === "dev") {
    try {
      const projectRoot = findProjectRoot();
      const webDir = join(projectRoot, "codeindex-web");
      log.debug(`Project root: ${projectRoot}`);
      log.debug(`Web dir: ${webDir}`);
      await viteManager.start(webDir);
      log.info(`Vite dev server ready on port ${VITE_PORT}`);
    } catch (err) {
      log.warn(`Vite dev server failed: ${(err as Error).message}`);
      log.info("Falling back to API server for static files");
    }
  }

  const url = await getMainViewUrl();

  const mainWindow = new BrowserWindow({
    title: "CodeIndex",
    url,
    frame: { width: 1400, height: 900, x: 100, y: 100 },
    titleBarStyle: "hiddenInset",
    rpc,
  });

  if (serverManager.isRunning) {
    mainWindow.webview.rpc?.send.serverReady({
      port: SERVER_PORT,
      url: `http://localhost:${SERVER_PORT}`,
    });
  }

  mainWindow.on("close", () => {
    serverManager.stop();
    viteManager.stop();
    process.exit(0);
  });

  log.info("Ready!");
}

boot().catch((err) => {
  log.error(`Fatal: ${err}`);
  serverManager.stop();
  viteManager.stop();
  process.exit(1);
});
