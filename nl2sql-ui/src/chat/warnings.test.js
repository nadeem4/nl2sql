import { test } from "node:test";
import assert from "node:assert/strict";
import { visibleWarnings } from "./warnings.js";

const w = (code, message, detail = {}) => ({ code, node: "n", message, sub_query_id: null, detail });

test("a warning about an attempt the run replaced is not shown", () => {
  assert.deepEqual(visibleWarnings({ warnings: [w("A", "x", { recovered: true }), w("B", "y")] }).map((x) => x.code), ["B"]);
});

test("the same warning twice shows once", () => {
  assert.equal(visibleWarnings({ warnings: [w("B", "y"), w("B", "y")] }).length, 1);
});

test("a truncated result adds a notice", () => {
  const out = visibleWarnings({ warnings: [], truncated: true });
  assert.match(out[0].message, /first rows/i);
});

test("no result or no warnings is an empty list", () => {
  assert.deepEqual(visibleWarnings(null), []);
  assert.deepEqual(visibleWarnings({}), []);
});
