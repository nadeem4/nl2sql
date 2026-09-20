import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// The built page is committed into the Python package so that
// `pip install "nl2sql-engine[demo]"` needs no Node toolchain. One
// self-contained index.html keeps the package data rule to a single line.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: {
    outDir: "../../packages/nl2sql/src/nl2sql/cli/demo/playground/static",
    emptyOutDir: true,
    assetsInlineLimit: 100000000,
    cssCodeSplit: false,
  },
});
