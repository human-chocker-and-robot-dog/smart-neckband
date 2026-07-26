import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        live: resolve(__dirname, "index.html"),
        dashboard: resolve(__dirname, "dashboard.html")
      }
    }
  },
  optimizeDeps: {
    entries: ["index.html", "dashboard.html"]
  },
  server: {
    watch: {
      ignored: ["**/data/**"]
    }
  }
});
