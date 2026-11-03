// A drift check as the Drift panel reads it. The fixtures are the API's own `DriftReport`s (written by the route test).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { driftSummary, sortReports } from "./drift.js";

const read = (name) => JSON.parse(readFileSync(new URL(`../api/fixtures/${name}`, import.meta.url), "utf8"));
const drifted = read("DriftReport.json");
const quiet = read("DriftReportUnchanged.json");

test("a drifted report lists every change in words, tables first", () => {
  const out = driftSummary(drifted);
  assert.equal(out.label, "Changed since the index was built");
  assert.equal(out.tone, "warn");
  assert.deepEqual(out.changes, [
    "Added table main.orders",
    "Dropped column main.tracks.composer",
    "Changed main.tracks.unit_price: type NUMERIC(10,2) to NUMERIC(12,2)",
  ]);
});

test("its impact says what the change breaks, by kind", () => {
  const out = driftSummary(drifted);
  assert.deepEqual(out.impact, [{ label: "Verified queries", lines: ["top_composers: reads dropped column tracks.composer"] }]);
});

test("an unchanged report has no changes and no impact, and is quiet", () => {
  const out = driftSummary(quiet);
  assert.equal(out.label, "Matches the index");
  assert.equal(out.tone, "ok");
  assert.deepEqual(out.changes, []);
  assert.deepEqual(out.impact, []);
});

test("foreign keys, a new column and an engine type change are told too", () => {
  const report = {
    datasource_id: "d", status: "drifted", checked_at: "2026-01-01T00:00:00Z", error: null, live_version: "v2",
    diff: {
      tables_added: [], tables_dropped: [{ schema_name: "main", table_name: "old" }],
      columns_added: [{ table: { schema_name: "main", table_name: "t" }, column_name: "c" }],
      columns_dropped: [], columns_changed: [],
      foreign_keys_added: [{ table: { schema_name: "main", table_name: "a" }, columns: ["x"], referred_table: { schema_name: "main", table_name: "b" }, referred_columns: ["y"] }],
      foreign_keys_dropped: [],
      engine_type: ["sqlite", "duckdb"],
    },
    impact: { verified_queries: [], glossary_items: ["revenue"], policy_rules: [{ role: "viewer", kind: "deny", pattern: "main.old" }], preferred_tables: ["main.old"] },
  };
  const out = driftSummary(report);
  assert.deepEqual(out.changes, [
    "Dropped table main.old",
    "Added column main.t.c",
    "Added foreign key main.a (x) to main.b (y)",
    "Engine type changed from sqlite to duckdb",
  ]);
  assert.deepEqual(out.impact, [
    { label: "Glossary items", lines: ["revenue"] },
    { label: "Policy rules", lines: ["viewer: deny main.old"] },
    { label: "Preferred tables", lines: ["main.old"] },
  ]);
});

test("not indexed and failed reports say so, with the error", () => {
  const base = { datasource_id: "d", checked_at: "2026-01-01T00:00:00Z", diff: null, impact: null, live_version: null };
  assert.equal(driftSummary({ ...base, status: "not_indexed", error: null }).label, "Not indexed yet");
  const failed = driftSummary({ ...base, status: "failed", error: "Could not connect." });
  assert.equal(failed.label, "Could not check");
  assert.equal(failed.tone, "fault");
  assert.equal(failed.error, "Could not connect.");
});

test("reports are listed with the ones that need attention first, then by name", () => {
  const rows = [
    { datasource_id: "b", status: "unchanged" }, { datasource_id: "a", status: "unchanged" },
    { datasource_id: "d", status: "failed" }, { datasource_id: "c", status: "drifted" },
    { datasource_id: "e", status: "not_indexed" },
  ];
  assert.deepEqual(sortReports(rows).map((r) => r.datasource_id), ["c", "d", "e", "a", "b"]);
});
