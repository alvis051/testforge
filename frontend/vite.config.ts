import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 127.0.0.1, not "localhost": uvicorn binds IPv4 by default, while Node
    // resolves "localhost" to ::1 first — so anything else holding IPv6 :8000
    // silently answers the proxy instead of the API.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
