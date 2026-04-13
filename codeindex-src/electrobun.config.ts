import type { ElectrobunConfig } from "electrobun";

export default {
  app: {
    name: "CodeIndex",
    identifier: "dev.codeindex.app",
    version: "1.0.0",
  },
  build: {
    bun: {
      entrypoint: "src/bun/index.ts",
    },
    // Vite builds to codeindex-web/dist/, copy into app bundle
    copy: {
      "codeindex-web/dist/index.html": "views/mainview/index.html",
      "codeindex-web/dist/assets": "views/mainview/assets",
    },
    watchIgnore: ["codeindex-web/dist/**", "codeindex/dist/**"],
    mac: {
      bundleCEF: false,
    },
    linux: {
      bundleCEF: false,
    },
    win: {
      bundleCEF: false,
    },
  },
} satisfies ElectrobunConfig;
