import { test } from "node:test";
import assert from "node:assert/strict";
import { rendererFor } from "./output.js";

test("a ToolOutput is rendered by its kind", () => {
  for (const kind of ["chart", "mermaid", "markdown", "json"]) {
    assert.equal(rendererFor({ kind, title: "t", body: "b" }), kind);
  }
});

test("a kind this page does not know, or no output, is shown as plain text", () => {
  assert.equal(rendererFor({ kind: "hologram", title: "t", body: "b" }), "text");
  assert.equal(rendererFor(null), "text");
  assert.equal(rendererFor(undefined), "text");
});

import { chartSpecOf } from "./output.js";

const spec = { kind: "bar", title: "t", data: { trace_id: "a", result_id: "b", columns: [], rows: [], truncated: false } };

test("a chart body is a ChartSpec object or its JSON text", () => {
  assert.deepEqual(chartSpecOf({ kind: "chart", body: spec }), spec);
  assert.deepEqual(chartSpecOf({ kind: "chart", body: JSON.stringify(spec) }), spec);
});

test("a chart body that is not a ChartSpec is not drawn", () => {
  assert.equal(chartSpecOf({ kind: "chart", body: "not json" }), null);
  assert.equal(chartSpecOf({ kind: "chart", body: { kind: "bar" } }), null);
  assert.equal(chartSpecOf({ kind: "chart", body: null }), null);
});
