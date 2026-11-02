import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { activeCount, filtersBody, inputType, isComplete, refreshBody, setValues } from "./filters.js";

const view = JSON.parse(readFileSync(new URL("../api/fixtures/DashboardView.json", import.meta.url), "utf8"));
const IN = view.dashboard.filters[0];
const BETWEEN = { id: "f2", label: "Order date", column: "chinook.main.orders.at", kind: "temporal", op: "between" };
const AMOUNT = { id: "f3", label: "Amount", column: "chinook.main.orders.total", kind: "number", op: "between" };

test("setValues keeps a selection and drops a filter whose values are empty", () => {
  let s = setValues({}, "f1", ["US", "FR"]);
  assert.deepEqual(s, { f1: ["US", "FR"] });
  s = setValues(s, "f2", ["2026-01-01", ""]);
  assert.deepEqual(Object.keys(s), ["f1", "f2"]);
  assert.deepEqual(setValues(s, "f1", []), { f2: ["2026-01-01", ""] });
});

test("an in filter needs one value, a between filter both ends", () => {
  assert.equal(isComplete(IN, ["US"]), true);
  assert.equal(isComplete(IN, []), false);
  assert.equal(isComplete(BETWEEN, ["2026-01-01", "2026-02-01"]), true);
  assert.equal(isComplete(BETWEEN, ["2026-01-01", ""]), false);
  assert.equal(isComplete(BETWEEN, undefined), false);
});

test("the refresh body sends in values and between ends, by filter id", () => {
  const filters = [IN, BETWEEN, AMOUNT];
  const selection = { f1: ["US", "FR"], f2: ["2026-01-01", "2026-02-01"], f3: ["10", "99.5"] };
  assert.deepEqual(filtersBody(filters, selection), {
    f1: ["US", "FR"], f2: ["2026-01-01", "2026-02-01"], f3: [10, 99.5],
  });
});

test("an incomplete or unknown filter is left out of the body", () => {
  assert.deepEqual(filtersBody([IN, BETWEEN], { f1: [], f2: ["2026-01-01", ""], gone: ["x"] }), {});
  assert.equal(activeCount([IN, BETWEEN], { f1: ["US"], f2: ["2026-01-01", ""] }), 1);
});

test("refreshBody names a tile, the filters and the granted approvals only when there are some", () => {
  assert.deepEqual(refreshBody({}), {});
  assert.deepEqual(refreshBody({ tileId: "t1" }), { tile_id: "t1" });
  assert.deepEqual(
    refreshBody({ filters: [IN], selection: { f1: ["US"] }, approvals: ["r1"] }),
    { filters: { f1: ["US"] }, approval_ids: ["r1"] },
  );
  assert.deepEqual(refreshBody({ filters: [IN], selection: {} }), {});
});

test("the input for a between filter follows the column's kind", () => {
  assert.equal(inputType(BETWEEN), "date");
  assert.equal(inputType(AMOUNT), "number");
  assert.equal(inputType(IN), "text");
});
