// The engine keys `origins` per field (`tables.Invoice.description`, `columns.Invoice.Total.role`), never per item.
import { test } from "node:test";
import assert from "node:assert/strict";
import { originOf } from "./origin.js";

const origins = {
  "tables.Invoice.description": "database",
  "columns.Invoice.Total.role": "derived",
  "columns.Invoice.Total.description": "knowledge",
  "terms.revenue.definition": "knowledge",
};

test("a table's origin is its description's", () => {
  assert.equal(originOf(origins, "tables", "Invoice"), "database");
});

test("a column's origin is its description's, else its first field's", () => {
  assert.equal(originOf(origins, "columns", "Invoice.Total"), "knowledge");
  assert.equal(originOf({ "columns.Invoice.Total.role": "derived" }, "columns", "Invoice.Total"), "derived");
});

test("a name that is only the start of another does not match, and nothing recorded gives null", () => {
  assert.equal(originOf({ "tables.InvoiceLine.description": "database" }, "tables", "Invoice"), null);
  assert.equal(originOf(undefined, "tables", "Invoice"), null);
});
