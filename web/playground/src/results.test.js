// Run with `npm test`. The Rows station reads a run's saved results from
// `result_manifest` and pages through them from the server: which tabs show,
// which page it asks for, the range it says, the cut notice and the CSV link.
// Also the per-sub-query heads the Plan and SQL stations use.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PAGE_SIZE, csvUrl, firstPage, pageNav, pageRange, pageUrl, resultTabs, rowCount, subQueryHeads, truncatedNotice,
} from "./results.js";

const longest = { node_id: "combine_g1", columns: ["genre", "track", "ms"], row_count: 3340, truncated: false };

test("resultTabs: no tabs for a single result, one per result for more", () => {
  assert.deepEqual(resultTabs([]), []);
  assert.deepEqual(resultTabs(null), []);
  assert.deepEqual(resultTabs([longest]), []);

  const tabs = resultTabs([longest, { node_id: "sq_2", columns: ["n"], row_count: 1, truncated: false }]);
  assert.deepEqual(tabs, [
    { id: "combine_g1", label: "combine_g1", rows: "3,340 rows" },
    { id: "sq_2", label: "sq_2", rows: "1 row" },
  ]);
});

test("pageUrl asks the paging endpoint for one page, 100 rows by default", () => {
  assert.equal(PAGE_SIZE, 100);
  assert.equal(pageUrl("run-1", "combine_g1", 0), "/api/query/run-1/results/combine_g1?offset=0&limit=100");
  assert.equal(pageUrl("run-1", "combine_g1", 200, 50), "/api/query/run-1/results/combine_g1?offset=200&limit=50");
  // Ids are escaped, so a strange one cannot reach another route.
  assert.equal(pageUrl("a/b", "c?d", 0), "/api/query/a%2Fb/results/c%3Fd?offset=0&limit=100");
});

test("csvUrl points at the full-result CSV endpoint, not a sample", () => {
  assert.equal(csvUrl("run-1", "combine_g1"), "/api/query/run-1/results/combine_g1.csv");
  assert.equal(csvUrl("a b", "x"), "/api/query/a%20b/results/x.csv");
});

test("pageNav: previous steps back a page, next follows the server's next_offset", () => {
  assert.deepEqual(pageNav({ offset: 0, limit: 100, total_rows: 3340, next_offset: 100 }), { prev: null, next: 100 });
  assert.deepEqual(pageNav({ offset: 100, limit: 100, total_rows: 3340, next_offset: 200 }), { prev: 0, next: 200 });
  assert.deepEqual(pageNav({ offset: 3300, limit: 100, total_rows: 3340, next_offset: null }), { prev: 3200, next: null });
  assert.deepEqual(pageNav({ offset: 0, limit: 100, total_rows: 0, next_offset: null }), { prev: null, next: null });
  // An offset that is not a multiple of the page still steps back to the start, never below it.
  assert.deepEqual(pageNav({ offset: 40, limit: 100, total_rows: 45, next_offset: null }), { prev: 0, next: null });
});

test("pageRange says which rows are on screen out of how many", () => {
  assert.equal(pageRange({ offset: 0, rows: new Array(100), total_rows: 3340 }), "Rows 1–100 of 3,340");
  assert.equal(pageRange({ offset: 3300, rows: new Array(40), total_rows: 3340 }), "Rows 3,301–3,340 of 3,340");
  assert.equal(pageRange({ offset: 0, rows: [[1]], total_rows: 1 }), "Row 1 of 1");
  assert.equal(pageRange({ offset: 0, rows: [], total_rows: 0 }), "No rows");
});

test("rowCount is the header's total", () => {
  assert.equal(rowCount(longest), "3,340 rows");
  assert.equal(rowCount({ row_count: 1 }), "1 row");
  assert.equal(rowCount({ row_count: 0 }), "0 rows");
});

test("firstPage: an empty result needs no request; anything else is fetched", () => {
  assert.equal(firstPage(longest), null);
  assert.deepEqual(firstPage({ node_id: "x", columns: ["a"], row_count: 0, truncated: false }),
    { columns: ["a"], rows: [], offset: 0, limit: PAGE_SIZE, total_rows: 0, next_offset: null });
});

test("truncatedNotice: only for a cut result, naming the ceiling it hit", () => {
  assert.equal(truncatedNotice(longest, []), null);
  const cut = { ...longest, row_count: 1000, truncated: true };
  // The ceiling is the sub-query's own row cap when the run reports it...
  assert.equal(truncatedNotice(cut, [{ truncated: true, row_cap: 1000 }]),
    "Cut at 1,000 rows, so some are missing. Narrow the question to get them all.");
  // ...and the saved row count when it does not.
  assert.equal(truncatedNotice(cut, []),
    "Cut at 1,000 rows, so some are missing. Narrow the question to get them all.");
});

test("subQueryHeads: nothing for one sub-query, a numbered head for each of several", () => {
  assert.deepEqual(subQueryHeads([]), []);
  assert.deepEqual(subQueryHeads([{ id: "sq_1", intent: "x" }]), []);
  assert.deepEqual(subQueryHeads([
    { id: "sq_1", intent: "Tracks per genre", datasource_id: "chinook" },
    { id: "sq_2", intent: "", datasource_id: "northwind" },
  ]), [
    { key: "sq_1", title: "Sub-query 1 of 2", detail: "Tracks per genre · chinook" },
    { key: "sq_2", title: "Sub-query 2 of 2", detail: "northwind" },
  ]);
});
