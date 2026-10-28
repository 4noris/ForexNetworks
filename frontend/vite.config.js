import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import process from "node:process";

const backendTarget = process.env.VITE_PROXY_TARGET || "http://127.0.0.1:8000";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: backendTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
