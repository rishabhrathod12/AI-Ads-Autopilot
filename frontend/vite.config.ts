import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  // API_PROXY_TARGET lets a developer point at a backend on another port (e.g. 8010).
  const env = loadEnv(mode, ".", "");
  return {
    plugins: [react()],
    server: {
      port: 5173,
      // Dev-only proxy so the browser talks to one origin; no secrets involved.
      proxy: {
        "/api": {
          target: env.API_PROXY_TARGET ?? "http://127.0.0.1:8000",
          rewrite: (p: string) => p.replace(/^\/api/, ""),
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test-setup.ts"],
      globals: false,
    },
  };
});
