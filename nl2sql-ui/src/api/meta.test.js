import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { getAbout, getInspectDefaults, getMeta, inspectIndex, startDemo } from "./meta.js";

test("getMeta reads GET /api/v1/meta", async () => {
  const body = readFileSync(new URL("./fixtures/Meta.json", import.meta.url), "utf8");
  const urls = [];
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return new Response(body, { status: 200, headers: { "content-type": "application/json" } });
  };
  assert.equal((await getMeta()).caller.user_id, "static");
  assert.deepEqual(urls, ["/api/v1/meta"]);
});

test("the pipeline, the retrieval defaults, a search and the demo job use their v1 routes", async () => {
  const seen = [];
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.fetch = async (url, init = {}) => {
    seen.push([init.method, String(url), init.body ? JSON.parse(init.body) : undefined]);
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  };
  await getAbout();
  await getInspectDefaults();
  await inspectIndex({ query: "genre", k: 3 });
  await startDemo();
  assert.deepEqual(seen, [
    ["GET", "/api/v1/about", undefined],
    ["GET", "/api/v1/index/inspect", undefined],
    ["POST", "/api/v1/index/inspect", { query: "genre", k: 3 }],
    ["POST", "/api/v1/jobs/demo", {}],
  ]);
});
