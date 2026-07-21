import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    proxy: {
      "/v1": "http://127.0.0.1:7337",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
