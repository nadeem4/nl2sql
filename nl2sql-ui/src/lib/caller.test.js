import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { callerOf, mayAdminister } from "./caller.js";

const meta = JSON.parse(readFileSync(new URL("../api/fixtures/Meta.json", import.meta.url), "utf8"));

test("the caller is read from the /meta fixture", () => {
  assert.deepEqual(callerOf(meta), { id: "static", admin: false });
});

test("no caller in /meta, or no /meta yet, is no caller", () => {
  assert.equal(callerOf(null), null);
  assert.equal(callerOf({ mode: "demo", auth: "api_keys" }), null);
});

test("a hosted visitor has no id and is never an admin", () => {
  assert.deepEqual(callerOf({ caller: { admin: false } }), { id: null, admin: false });
});

test("admin routes are offered unless /meta says the caller is not an admin", () => {
  assert.equal(mayAdminister(null), true); // unknown: the server's 403 decides
  assert.equal(mayAdminister({ id: "ada", admin: true }), true);
  assert.equal(mayAdminister({ id: "static", admin: false }), false);
});
