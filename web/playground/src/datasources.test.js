import { test } from "node:test";
import assert from "node:assert/strict";
import { answeredDatasources, datasourceNames } from "./datasources.js";

test("the switcher lists exactly what /api/meta reports, in that order", () => {
  const meta = { dataset: "chinook", datasources: ["chinook", "support", "webanalytics"] };
  assert.deepEqual(datasourceNames(meta), ["chinook", "support", "webanalytics"]);
});

test("a server that reports no datasources is one database's worth", () => {
  assert.deepEqual(datasourceNames({ dataset: "chinook" }), ["chinook"]);
  assert.deepEqual(datasourceNames({ dataset: "chinook", datasources: [] }), ["chinook"]);
  assert.deepEqual(datasourceNames({ dataset: "chinook", datasources: [null, ""] }), ["chinook"]);
});

test("nothing is listed before /api/meta has answered", () => {
  assert.deepEqual(datasourceNames(null), []);
  assert.deepEqual(datasourceNames({}), []);
});

test("the run says which database the resolver picked", () => {
  const result = { sub_queries: [{ id: "sq1", datasource_id: "support" }] };
  assert.deepEqual(answeredDatasources(result), ["support"]);
});

test("sub-queries on one database name it once", () => {
  const result = { sub_queries: [{ datasource_id: "chinook" }, { datasource_id: "chinook" }] };
  assert.deepEqual(answeredDatasources(result), ["chinook"]);
});

test("a run that never reached a sub-query names no database", () => {
  assert.deepEqual(answeredDatasources({ sub_queries: [] }), []);
  assert.deepEqual(answeredDatasources({ sub_queries: [{ id: "sq1", datasource_id: "" }] }), []);
  assert.deepEqual(answeredDatasources(null), []);
});
