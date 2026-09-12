/// <reference types="vitest/config" />

import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The backend the dev server proxies to. Overridable so `npm run dev` can
// point at a container or another box without editing this file.
const BACKEND = process.env.GAGGICLANKER_BACKEND ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    // Same-origin in production (FastAPI serves web/dist), so the dev server
    // proxies rather than the app talking cross-origin: one code path, no CORS
    // preflight in development that production never sees.
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
      "/health": { target: BACKEND, changeOrigin: true },
      // Convenience for `npm run gen:api` against a running dev server.
      "/openapi.json": {
        target: BACKEND,
        changeOrigin: true,
        rewrite: () => "/api/openapi.json",
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    exclude: ["node_modules/**", "dist/**"],
    restoreMocks: true,
  },
});
