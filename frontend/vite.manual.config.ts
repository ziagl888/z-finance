import { fileURLToPath } from "node:url";

import { mergeConfig } from "vite";

import appConfig from "./vite.config.ts";

// The manual as a static site for GitHub Pages (.github/workflows/pages.yml): the same chapters and
// the same Handbook component as the in-app manual, without the backend. Build with
// `npx vite build --config vite.manual.config.ts` (output: frontend/dist/manual/).
export default mergeConfig(appConfig, {
  root: fileURLToPath(new URL("./src/help/site", import.meta.url)),
  publicDir: fileURLToPath(new URL("./public", import.meta.url)),
  // Relative asset URLs: Pages serves the site below /<repository>/.
  base: "./",
  build: {
    outDir: fileURLToPath(new URL("./dist/manual", import.meta.url)),
    emptyOutDir: true,
  },
});
