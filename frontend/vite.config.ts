import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The Python API binds to 127.0.0.1:8000 and has no CORS on purpose (local only). The dev server forwards
// /api/* to it, so the browser talks to one origin and the API needs no changes. Override with API_TARGET.
const target = process.env.API_TARGET ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": {
    target,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, proxy },
  preview: { host: "127.0.0.1", port: 4173, proxy },
});
