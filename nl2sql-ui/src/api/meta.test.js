import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { getMeta } from "./meta.js";

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
