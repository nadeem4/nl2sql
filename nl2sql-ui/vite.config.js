import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build is `index.html` plus hashed chunks (D214), written into the API package: nl2sql-api serves it and the
// wheel carries it, so `pip install nl2sql-api` needs no Node toolchain (D226). The folder is not committed.
// Until X1b, `nl2sql demo` keeps serving the single page already committed in the engine package.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../packages/api/src/nl2sql_api/web/static",
    emptyOutDir: true,
  },
});
