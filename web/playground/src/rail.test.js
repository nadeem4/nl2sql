// Run with `npm test`. The rail: the index's one-line summary and when it
// opens itself, the datasource switcher's shape, and the scroll-edge fades.
import { test } from "node:test";
import assert from "node:assert/strict";
import { indexExpanded, indexSummary } from "./indexHealth.js";
import { switcherKind } from "./datasources.js";
import { fadeEdges } from "./railFade.js";

const NOW = Date.parse("2026-10-03T12:00:00Z");
const idle = { state: "idle", steps: [] };

test("indexSummary: a healthy index is one line with its size and age", () => {
  const health = { status: "ok", total: 162, built_at: "2026-10-03T11:57:00Z", datasources: [] };

  assert.deepEqual(indexSummary(health, idle, NOW),
    { lead: "Search index fresh.", detail: "162 entries, built 3 minutes ago." });
  assert.deepEqual(indexSummary({ ...health, total: 1, built_at: null }, idle, NOW),
    { lead: "Search index fresh.", detail: "1 entry." });
});

test("indexSummary: the age is the datasource's own build when it has one", () => {
  const health = { status: "ok", total: 1200, built_at: "2026-10-01T12:00:00Z",
    datasources: [{ datasource_id: "chinook", built_at: "2026-10-03T11:00:00Z" }] };

  assert.equal(indexSummary(health, idle, NOW, "chinook").detail, "1,200 entries, built 1 hour ago.");
});

test("indexSummary: an index that needs work says so first", () => {
  assert.equal(indexSummary({ status: "stale", total: 9 }, idle, NOW).lead, "Search index out of date.");
  assert.equal(indexSummary({ status: "empty", total: 0 }, idle, NOW).lead, "Search index empty.");
  assert.equal(indexSummary({ status: "missing", total: 0 }, idle, NOW).lead, "No search index yet.");
  assert.equal(indexSummary({ status: "ok", total: 9 }, { state: "running", steps: ["Reading"] }, NOW).lead,
    "Rebuilding the search index.");
  assert.equal(indexSummary({ status: "ok", total: 9 }, { state: "failed", steps: [] }, NOW).lead,
    "The last rebuild failed.");
  assert.equal(indexSummary(null, idle, NOW).lead, "Checking the search index.");
});

test("indexExpanded: open itself only when the index needs attention", () => {
  const index = (status, state = "idle") => ({ health: { status }, job: { state, steps: [] } });

  assert.equal(indexExpanded(index("ok")), false);
  assert.equal(indexExpanded(index("stale")), true);
  assert.equal(indexExpanded(index("empty")), true);
  assert.equal(indexExpanded(index("missing")), true);
  assert.equal(indexExpanded(index("ok", "running")), true);
  assert.equal(indexExpanded(index("ok", "failed")), true);
  assert.equal(indexExpanded(null), false);
});

test("switcherKind: one database is no choice, up to four a segmented control, more a select", () => {
  assert.equal(switcherKind([]), "none");
  assert.equal(switcherKind(["chinook"]), "none");
  assert.equal(switcherKind(["a", "b"]), "segmented");
  assert.equal(switcherKind(["a", "b", "c", "d"]), "segmented");
  assert.equal(switcherKind(["a", "b", "c", "d", "e"]), "select");
  assert.equal(switcherKind(null), "none");
});

test("fadeEdges: fade only an edge that has more content past it", () => {
  // Nothing to scroll.
  assert.deepEqual(fadeEdges({ scrollTop: 0, scrollHeight: 500, clientHeight: 500 }), { top: false, bottom: false });
  // At the top of a long rail.
  assert.deepEqual(fadeEdges({ scrollTop: 0, scrollHeight: 1500, clientHeight: 500 }), { top: false, bottom: true });
  // Part way down.
  assert.deepEqual(fadeEdges({ scrollTop: 300, scrollHeight: 1500, clientHeight: 500 }), { top: true, bottom: true });
  // At the bottom, allowing for a fractional scroll position.
  assert.deepEqual(fadeEdges({ scrollTop: 999.5, scrollHeight: 1500, clientHeight: 500 }), { top: true, bottom: false });
  assert.deepEqual(fadeEdges(null), { top: false, bottom: false });
});
