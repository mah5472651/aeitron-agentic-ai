import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  publicDir: "../../image.png",
  server: {
    port: 4173,
    proxy: {
      "/v1": "http://127.0.0.1:8090",
      "/health": "http://127.0.0.1:8090",
    },
  },
});
