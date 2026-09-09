import { defineConfig } from "vitest/config";
import solidPlugin from "vite-plugin-solid";

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
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
});
