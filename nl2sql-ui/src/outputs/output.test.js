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
