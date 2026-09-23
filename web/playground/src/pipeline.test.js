// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { countModelSteps, modelUsed, pipelineRows } from "./pipeline.js";

const STEPS = [
  { node: "datasource_resolver", label: "Answerability check", does: "a.", kind: "model",
    agent: "datasourceresolver", parent: null, provider: "openai", model: "gpt-5.4" },
  { node: "sql_agent", label: "SQL agent", does: "b.", kind: "code", agent: null, parent: null,
    provider: null, model: null },
  { node: "ast_planner", label: "Query planner", does: "c.", kind: "model", agent: "astplanner",
    parent: "sql_agent", provider: "anthropic", model: "claude-opus-5" },
  { node: "generator", label: "SQL writer", does: "d.", kind: "code", agent: null,
    parent: "sql_agent", provider: null, model: null },
];

test("pipelineRows lists every step with no run, and nests the subgraph's own", () => {
  const rows = pipelineRows(STEPS, null);

  assert.deepEqual(rows.map((r) => r.node),
    ["datasource_resolver", "sql_agent", "ast_planner", "generator"]);
  assert.deepEqual(rows.map((r) => r.depth), [0, 0, 1, 1]);
  assert.ok(rows.every((r) => r.usage === null && r.seconds === undefined && r.ran === false));
});

test("pipelineRows attaches the last run's tokens and time to the steps that ran", () => {
  const result = {
    usage: {
      nodes: {
        datasource_resolver: { calls: 1, input_tokens: 900, cached_input_tokens: 768, output_tokens: 12 },
        ast_planner: { calls: 2, input_tokens: 4200, cached_input_tokens: 0, output_tokens: 610 },
      },
      calls: [
        { node: "ast_planner", model: "claude-opus-5" },
        { node: "datasource_resolver", model: "gpt-5.4" },
      ],
    },
    timings: { LangGraph: 6.5, datasource_resolver: 0.8, ast_planner: 3.9, generator: 0.01 },
  };

  const rows = pipelineRows(STEPS, result);
  const by = Object.fromEntries(rows.map((r) => [r.node, r]));

  assert.equal(by.ast_planner.usage.input_tokens, 4200);
  assert.equal(by.ast_planner.seconds, 3.9);
  assert.equal(by.ast_planner.ran, true);
  assert.equal(by.ast_planner.retried, true);
  // A deterministic step has time but never tokens.
  assert.equal(by.generator.usage, null);
  assert.equal(by.generator.seconds, 0.01);
  assert.equal(by.generator.ran, true);
  // A step that never ran stays blank rather than reading as zero.
  assert.equal(by.sql_agent.ran, false);
});

test("pipelineRows keeps the described order even when timings report another", () => {
  const rows = pipelineRows(STEPS, { timings: { generator: 1, datasource_resolver: 2 } });

  assert.deepEqual(rows.map((r) => r.node),
    ["datasource_resolver", "sql_agent", "ast_planner", "generator"]);
});

test("modelUsed names the model the run actually called for a step", () => {
  const usage = { calls: [{ node: "ast_planner", model: "claude-opus-5" },
                          { node: "ast_planner", model: "claude-opus-5" },
                          { node: "refiner", model: "gpt-5.4" }] };

  assert.equal(modelUsed(usage, "ast_planner"), "claude-opus-5");
  assert.equal(modelUsed(usage, "refiner"), "gpt-5.4");
  assert.equal(modelUsed(usage, "decomposer"), null);
  assert.equal(modelUsed(null, "ast_planner"), null);
});

test("modelUsed reports both when a step was served by two models", () => {
  const usage = { calls: [{ node: "ast_planner", model: "gpt-5.4" },
                          { node: "ast_planner", model: "gpt-4.1" }] };

  assert.equal(modelUsed(usage, "ast_planner"), "gpt-5.4, gpt-4.1");
});

test("countModelSteps counts the steps a model decides", () => {
  assert.equal(countModelSteps(STEPS), 2);
  assert.equal(countModelSteps(null), 0);
});
