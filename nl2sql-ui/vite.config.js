import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build is `index.html` plus hashed chunks, written into the API package: nl2sql-api serves it and the
// wheel carries it, so `pip install nl2sql-api` needs no Node toolchain. The folder is not committed.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../packages/api/src/nl2sql_api/web/static",
    emptyOutDir: true,
  },
});
