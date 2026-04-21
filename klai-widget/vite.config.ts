import { defineConfig } from "vite";
import solidPlugin from "vite-plugin-solid";

export default defineConfig({
  plugins: [solidPlugin()],
  build: {
    lib: {
      entry: "src/main.ts",
      name: "KlaiWidget",
      formats: ["iife"],
      fileName: () => "klai-chat.js",
    },
    rollupOptions: {
      // No external deps — bundle everything
      external: [],
      output: {
        inlineDynamicImports: true,
      },
    },
    target: "es2020",
    minify: "terser",
    terserOptions: {
      compress: {
        drop_console: false,
        passes: 2,
      },
    },
    // Single output file
    cssCodeSplit: false,
    outDir: "dist",
    emptyOutDir: true,
  },
});
