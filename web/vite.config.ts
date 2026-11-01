import { fileURLToPath } from "node:url"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from "vite"
import { viteSingleFile } from "vite-plugin-singlefile"

// The build is pu's page-tier dashboard: one file with scripts, styles and
// fonts inlined, written over ../pu/dashboard.html (UNIT_STANDARDS.md,
// "Requirements on a page-tier unit", rule 1 as amended in holonic-node
// 6b50dfb). CI rebuilds it and fails if the committed file differs.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), viteSingleFile({ removeViteModuleLoader: true })],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: "../pu",
    emptyOutDir: false, // ../pu is the Python package; only dashboard.html is ours
    rollupOptions: { input: "dashboard.html" },
  },
})
