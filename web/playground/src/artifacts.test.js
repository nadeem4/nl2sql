// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { barShares, csvFileName, firstNumericColumn, minMaxShares, numericColumns, toCsv } from "./artifacts.js";

test("numericColumns: a column is numeric when its first non-null value is a number", () => {
  const rows = [[null, "a", 1], [2, "b", "x"]];
  assert.deepEqual(numericColumns(["p", "q", "r"], rows), [true, false, true]);
  assert.deepEqual(numericColumns(["p"], [[null]]), [false]);
});

test("firstNumericColumn is the first numeric column, or -1", () => {
  assert.equal(firstNumericColumn(["genre", "n", "m"], [["Rock", 835, 2]]), 1);
  assert.equal(firstNumericColumn(["genre"], [["Rock"]]), -1);
  assert.equal(firstNumericColumn([], []), -1);
});

test("barShares normalises to the largest magnitude", () => {
  assert.deepEqual(barShares([835, 386, 0]).map((b) => b.share), [1, 386 / 835, 0]);
  assert.ok(barShares([835, 386]).every((b) => !b.negative));
});

test("barShares measures a negative by its size and marks it", () => {
  assert.deepEqual(barShares([-50, 25]), [{ share: 1, negative: true }, { share: 0.5, negative: false }]);
});

test("barShares gives no bar for null or non-numbers, and zero for an all-zero column", () => {
  assert.deepEqual(barShares([null, "x", 4]), [null, null, { share: 1, negative: false }]);
  assert.deepEqual(barShares([0, 0]), [{ share: 0, negative: false }, { share: 0, negative: false }]);
  assert.deepEqual(barShares([]), []);
});

test("minMaxShares spreads a pool across its own range", () => {
  assert.deepEqual(minMaxShares([0.40, 0.38, 0.36]).map((s) => Number(s.toFixed(6))), [1, 0.5, 0]);
});

test("minMaxShares fills every bar when all values are equal, and skips non-numbers", () => {
  assert.deepEqual(minMaxShares([0.3, 0.3]), [1, 1]);
  assert.deepEqual(minMaxShares([null, 0.2, 0.4]), [null, 0, 1]);
  assert.deepEqual(minMaxShares([]), []);
});

test("toCsv writes a header and rows with CRLF, empty for NULL", () => {
  assert.equal(toCsv(["genre", "n"], [["Rock", 835], ["Jazz", null]]), "genre,n\r\nRock,835\r\nJazz,\r\n");
});

test("toCsv quotes commas, quotes and line breaks", () => {
  assert.equal(toCsv(["a"], [["R&B, Soul"], ['say "hi"'], ["two\nlines"]]), 'a\r\n"R&B, Soul"\r\n"say ""hi"""\r\n"two\nlines"\r\n');
});

test("toCsv defuses text a spreadsheet would run as a formula, but leaves numbers alone", () => {
  assert.equal(toCsv(["a"], [["=1+1"], ["@SUM(A1)"], [-5]]), "a\r\n'=1+1\r\n'@SUM(A1)\r\n-5\r\n");
});

test("toCsv writes booleans and objects as text", () => {
  assert.equal(toCsv(["a", "b"], [[true, { k: 1 }]]), 'a,b\r\ntrue,"{""k"":1}"\r\n');
});

test("csvFileName names the file after the run", () => {
  assert.equal(csvFileName({ trace_id: "abc123" }), "nl2sql-rows-abc123.csv");
  assert.equal(csvFileName(null), "nl2sql-rows.csv");
});
