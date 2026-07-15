import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // 允许局域网访问 (0.0.0.0)
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        ws: true, // 转发 WebSocket (agent 流式事件)
      },
    },
  },
});
