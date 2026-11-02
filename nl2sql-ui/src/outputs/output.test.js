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

import { needsRows, mergeFetched } from "./output.js";

test("a saved chart whose rows were stripped is fetched again", () => {
  const stripped = { kind: "bar", title: "Kept", data: { trace_id: "a", result_id: "b", rows: [] } };
  assert.equal(needsRows(stripped), true);
  assert.equal(needsRows({ ...stripped, data: { trace_id: "a", result_id: "b" } }), true);
  assert.equal(needsRows({ ...stripped, data: { ...stripped.data, rows: [[1]] } }), false);
  assert.equal(needsRows({ ...stripped, data: { rows: [] } }), false); // nothing to fetch it by
  assert.equal(needsRows({ kind: "table", data: { trace_id: "a", result_id: "b", rows: [] } }), true);
});

test("the fetched spec replaces the stored one but the stored title stays", () => {
  const stored = { kind: "bar", title: "Kept", data: { trace_id: "a", result_id: "b", rows: [] } };
  const fresh = { kind: "line", title: "Result", takeaway: "up", data: { trace_id: "a", result_id: "b", rows: [[1]] } };
  assert.deepEqual(mergeFetched(stored, fresh), { ...fresh, title: "Kept" });
});

test("a stripped chart body still counts as a ChartSpec", () => {
  const body = { kind: "bar", data: { trace_id: "a", result_id: "b" } };
  assert.deepEqual(chartSpecOf({ kind: "chart", body }), body);
});
