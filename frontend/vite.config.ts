import { fileURLToPath } from "node:url";

import { lingui, linguiTransformerBabelPreset } from "@lingui/vite-plugin";
import babel from "@rolldown/plugin-babel";
import react from "@vitejs/plugin-react";
import { defineConfig, searchForWorkspaceRoot } from "vite";

// The backend (FastAPI, port 8765) serves the production bundle from backend/zfinance/web/.
// In development (`just dev`) Vite serves the SPA on 5173 and proxies /api to the backend.
const BACKEND = "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [
    react(),
    // Compiles `src/locales/*.po` imports into message catalogs.
    lingui(),
    // Vite 8 (Rolldown) cannot expand Lingui macros itself. descriptorFields "message" keeps the
    // English source text in production: English has no catalog and renders it directly.
    babel({
      presets: [linguiTransformerBabelPreset({ descriptorFields: "message" })],
    }),
  ],
  build: {
    outDir: fileURLToPath(new URL("../backend/zfinance/web", import.meta.url)),
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // The manual chapters and their screenshots (src/help/manual.ts) are read from ../docs/manual
    // and ../docs/assets, outside this package.
    fs: {
      allow: [
        searchForWorkspaceRoot(process.cwd()),
        fileURLToPath(new URL("../docs/manual", import.meta.url)),
        fileURLToPath(new URL("../docs/assets", import.meta.url)),
      ],
    },
    proxy: {
      "/api": BACKEND,
    },
  },
});
