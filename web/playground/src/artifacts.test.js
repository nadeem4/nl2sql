// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { barShares, firstNumericColumn, minMaxShares, numericColumns } from "./artifacts.js";

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
