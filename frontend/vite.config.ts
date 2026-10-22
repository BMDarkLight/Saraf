import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // API_PROXY: point the dev UI at another backend, e.g. the VPS through `ssh -L 8765:127.0.0.1:8000`
  server: { port: 5173, proxy: { "/api": process.env.API_PROXY ?? "http://127.0.0.1:8000" } },
});
