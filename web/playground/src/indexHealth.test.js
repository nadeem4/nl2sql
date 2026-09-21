// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { countRows, needsRebuild, relativeTime, shortVersion, statusLine } from "./indexHealth.js";

test("countRows orders known kinds and names them in plain words", () => {
  const rows = countRows({ "schema.column": 64, "schema.table": 11, "schema.datasource": 1, "schema.relationship": 11 });
  assert.deepEqual(rows.map((r) => [r.label, r.count]), [
    ["datasource", 1],
    ["tables", 11],
    ["columns", 64],
    ["relationships", 11],
  ]);
});

test("countRows keeps unknown kinds after the known ones", () => {
  assert.deepEqual(countRows({ "schema.metric": 2, "schema.table": 1 }).map((r) => r.label), ["table", "metric"]);
  assert.deepEqual(countRows(null), []);
});

test("statusLine says what an empty index means for a question", () => {
  assert.match(statusLine({ status: "empty", total: 0 }), /every question will fail/);
  assert.match(statusLine({ status: "ok", total: 87 }), /^87 entries/);
  assert.equal(statusLine(null), "Checking the index.");
});

test("needsRebuild is true for anything but ok", () => {
  assert.equal(needsRebuild({ status: "ok" }), false);
  for (const status of ["empty", "missing", "stale"]) assert.equal(needsRebuild({ status }), true);
  assert.equal(needsRebuild(null), false);
});

test("relativeTime rounds down to the largest unit", () => {
  const now = Date.parse("2026-09-21T12:00:00Z");
  assert.equal(relativeTime("2026-09-21T11:59:30Z", now), "just now");
  assert.equal(relativeTime("2026-09-21T11:57:00Z", now), "3 minutes ago");
  assert.equal(relativeTime("2026-09-21T11:00:00Z", now), "1 hour ago");
  assert.equal(relativeTime("2026-09-19T12:00:00Z", now), "2 days ago");
  assert.equal(relativeTime(null, now), null);
});

test("shortVersion keeps the structure hash", () => {
  assert.equal(shortVersion("20260921204021_ceed80fe"), "ceed80fe");
  assert.equal(shortVersion("v1"), "v1");
  assert.equal(shortVersion(null), null);
});
