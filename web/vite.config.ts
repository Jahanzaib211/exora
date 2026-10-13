import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` serves on :5173 and forwards /api to `prospector serve` on :8090.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8090" },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // Long-lived vendor chunks: app changes do not invalidate the cached UI library.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          if (/[\\/](antd|@ant-design|rc-[^\\/]+)[\\/]/.test(id)) return "vendor-antd";
          if (/[\\/](react-markdown|remark-|micromark|mdast|unified|hast|unist|vfile)/.test(id)) return "vendor-markdown";
          if (/[\\/]@refinedev[\\/]/.test(id)) return "vendor-refine";
          return "vendor";
        },
      },
    },
  },
});
