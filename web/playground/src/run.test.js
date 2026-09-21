// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { deniedTables, formatSql, nodeLedger, planTables, humanCheck } from "./run.js";

test("formatSql breaks before each clause, outside string literals", () => {
  const sql =
    "SELECT t3.Name AS genre, COUNT(t1.Id) AS n FROM InvoiceLine AS t1 INNER JOIN Track AS t2 ON t1.TrackId = t2.TrackId " +
    "WHERE t3.Name = 'Rock FROM Hell' GROUP BY t3.Name ORDER BY n DESC LIMIT 1000";
  assert.equal(
    formatSql(sql),
    [
      "SELECT t3.Name AS genre, COUNT(t1.Id) AS n",
      "FROM InvoiceLine AS t1",
      "INNER JOIN Track AS t2 ON t1.TrackId = t2.TrackId",
      "WHERE t3.Name = 'Rock FROM Hell'",
      "GROUP BY t3.Name",
      "ORDER BY n DESC",
      "LIMIT 1000",
    ].join("\n")
  );
});

test("formatSql leaves empty input empty", () => {
  assert.equal(formatSql(""), "");
  assert.equal(formatSql(null), "");
});

test("deniedTables reads the table names out of SECURITY_VIOLATION errors", () => {
  const errors = [
    { error_code: "SECURITY_VIOLATION", message: "Role 'viewer' denied access to 'chinook.Customer'. Policy requires explicit 'datasource.table' allow." },
    { error_code: "SECURITY_VIOLATION", message: "Role 'viewer' denied access to 'chinook.Invoice'. Policy requires..." },
    { error_code: "TABLE_NOT_FOUND", message: "Table 'Customers' not found in relevant tables." },
  ];
  assert.deepEqual(deniedTables(errors), ["Customer", "Invoice"]);
  assert.deepEqual(deniedTables(undefined), []);
});

test("planTables lists the plan's table names", () => {
  assert.deepEqual(planTables({ tables: [{ name: "Track" }, { name: "Genre", schema_name: "main" }] }), ["Track", "Genre"]);
  assert.deepEqual(planTables(null), []);
});

test("nodeLedger follows execution order, nests the SQL agent's nodes, and flags retries", () => {
  const timings = {
    datasource_resolver: 0.2, decomposer: 0.01, schema_retriever: 0.001, ast_planner: 0.008,
    logical_validator: 0.001, refiner: 0.004, generator: 0.0003, executor: 0.004, sql_agent: 0.03,
    answer_synthesizer: 0.004, LangGraph: 0.25,
  };
  const nodes = {
    decomposer: { calls: 1, input_tokens: 1900 },
    ast_planner: { calls: 2, input_tokens: 22000 },
    refiner: { calls: 1, input_tokens: 8600 },
    answer_synthesizer: { calls: 1, input_tokens: 300 },
    aggregator_llm: { calls: 1, input_tokens: 5 },
  };
  const rows = nodeLedger({ nodes }, timings);
  assert.deepEqual(
    rows.map((r) => [r.name, r.depth]),
    [
      ["datasource_resolver", 0], ["decomposer", 0], ["schema_retriever", 0],
      ["sql_agent", 0], ["ast_planner", 1], ["logical_validator", 1], ["refiner", 1],
      ["generator", 1], ["executor", 1],
      ["answer_synthesizer", 0], ["aggregator_llm", 0],
    ]
  );
  const planner = rows.find((r) => r.name === "ast_planner");
  assert.equal(planner.retried, true);
  assert.equal(planner.seconds, 0.008);
  assert.equal(rows.find((r) => r.name === "decomposer").retried, false);
  assert.equal(rows.find((r) => r.name === "schema_retriever").usage, null);
  assert.equal(rows.find((r) => r.name === "aggregator_llm").seconds, undefined);
});

test("nodeLedger is empty with nothing to report", () => {
  assert.deepEqual(nodeLedger(null, null), []);
});

test("humanCheck names the three validator checks in plain words", () => {
  assert.equal(humanCheck("plan_present"), "Plan present");
  assert.equal(humanCheck("structure_and_schema"), "Tables and columns exist");
  assert.equal(humanCheck("policy"), "Role may read these tables");
  assert.equal(humanCheck("something_new"), "something new");
});
