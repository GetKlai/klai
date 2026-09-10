import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import solidPlugin from "vite-plugin-solid";

const widgetRoot = dirname(fileURLToPath(import.meta.url));
const geistFont = resolve(
  widgetRoot,
  "../klai-portal/frontend/public/fonts/geist-regular.woff2",
);

// Separate from vite.config.ts on purpose: the build config describes one
// IIFE bundle for a customer's page and must not grow test-only settings.
// Solid components have to be compiled by vite-plugin-solid here too, and
// the "development" resolve condition is what makes solid-js hand back its
// dev build (the production build has no reactive graph to render into a
// jsdom document).
export default defineConfig({
  plugins: [solidPlugin()],
  resolve: {
    conditions: ["development", "browser"],
  },
  // main.ts inlines the portal's geist-regular.woff2, which lives outside
  // klai-widget. Setting server.fs.allow replaces the inferred workspace
  // root, so keep the widget root and add exactly that one font file;
  // strict mode stays on and every other external path remains denied.
  server: {
    fs: {
      strict: true,
      allow: [widgetRoot, geistFont, `${geistFont}?inline`],
    },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
});
