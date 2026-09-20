# nl2sql playground UI

The React source for the page `nl2sql demo` serves.

## Rebuild

```bash
cd web/playground
npm install
npm run build
```

`npm run build` writes a single self-contained `index.html` (JS and CSS inlined
by `vite-plugin-singlefile`) into:

```
packages/nl2sql/src/nl2sql/cli/demo/playground/static/index.html
```

**That build output is committed on purpose.** The wheel has to contain the
finished page so `pip install "nl2sql-engine[demo]"` followed by `nl2sql demo`
works with no Node installed, and so CI never needs a JavaScript toolchain. The
trade is that a UI change is not live until you rebuild and commit the result --
if you edit anything under `src/`, run `npm run build` and commit
`static/index.html` in the same change.

## Develop

```bash
npm run dev
```

Vite serves the page on its own port. Run `nl2sql demo --no-browser` alongside
it and proxy or point `fetch` at `http://127.0.0.1:8765` to exercise the real
API; the app only calls `/api/meta`, `/api/schema` and `/api/ask`.

## Scope

React and Vite only -- no router, no state library, no component kit, no CSS
framework, no TypeScript. Plain JSX and plain CSS, kept small enough to read in
one sitting.
