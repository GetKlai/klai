/**
 * Electrobun RPC bridge — provides native desktop features when running
 * inside Electrobun, with graceful browser fallbacks.
 */

const isElectrobun = typeof window !== 'undefined' &&
  (window as any).__electrobun !== undefined;

export const desktop = {
  /** Whether the app is running inside Electrobun */
  isDesktop: isElectrobun,

  /** Open a file in the user's code editor */
  async openInEditor(path: string, line?: number): Promise<void> {
    if (!isElectrobun) {
      // Browser fallback: copy path to clipboard
      const target = line ? `${path}:${line}` : path;
      await navigator.clipboard?.writeText(target);
      return;
    }
    await (window as any).__electrobun.rpc.request.openInEditor({ path, line });
  },

  /** Open native directory picker */
  async selectDirectory(): Promise<string | null> {
    if (!isElectrobun) return null;
    const result = await (window as any).__electrobun.rpc.request.selectDirectory({});
    return result.path;
  },

  /** Get system info from main process */
  async getSystemInfo(): Promise<{
    platform: string;
    homedir: string;
    codeindexDir: string;
    serverPort: number;
  } | null> {
    if (!isElectrobun) return null;
    return (window as any).__electrobun.rpc.request.getSystemInfo({});
  },
};
