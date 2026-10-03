// Run with `npm test`. The three phases, step states and waterfall the
// Pipeline page draws, all derived from the server's own step list.
import { test } from "node:test";
import assert from "node:assert/strict";
import { pipelineRows } from "./pipeline.js";
import { PHASES, pipelinePhases, stepStates, waterfall } from "./pipelinePhases.js";

// The real list, as GET /api/pipeline returns it (nl2sql.pipeline.steps).
const step = (node, kind, parent = null) => ({ node, label: node, does: ".", kind, parent,
  agent: kind === "model" ? node : null, provider: kind === "model" ? "openai" : null,
  model: kind === "model" ? "gpt-5.4" : null });
const REAL = [
  step("datasource_resolver", "model"),
  step("decomposer", "model"),
  step("layer_router", "code"),
  step("sql_agent", "code"),
  step("schema_retriever", "code", "sql_agent"),
  step("ast_planner", "model", "sql_agent"),
  step("logical_validator", "code", "sql_agent"),
  step("retry_handler", "code", "sql_agent"),
  step("refiner", "model", "sql_agent"),
  step("generator", "code", "sql_agent"),
  step("executor", "code", "sql_agent"),
  step("aggregator", "code"),
  step("answer_synthesizer", "model"),
];

const nodes = (items) => items.map((it) => (it.type === "bracket"
  ? { bracket: it.parent.node, rows: it.rows.map((r) => r.node) }
  : it.row.node));

test("the real list falls into the three phases, the SQL agent's own steps in a bracket", () => {
  const phases = pipelinePhases(pipelineRows(REAL, null));

  assert.deepEqual(phases.map((p) => p.id), ["understand", "answer", "combine"]);
  assert.deepEqual(phases.map((p) => p.title),
    ["Understand the question", "Answer each sub-query", "Combine and explain"]);
  assert.deepEqual(nodes(phases[0].items), ["datasource_resolver", "decomposer"]);
  assert.deepEqual(nodes(phases[1].items), ["layer_router", {
    bracket: "sql_agent",
    rows: ["schema_retriever", "ast_planner", "logical_validator", "retry_handler", "refiner",
      "generator", "executor"],
  }]);
  assert.deepEqual(nodes(phases[2].items), ["aggregator", "answer_synthesizer"]);
  assert.equal(phases[1].note, "repeats per sub-query, and again when the checks refuse a plan");
});

test("every step lands in exactly one phase, in the order the server gave", () => {
  const phases = pipelinePhases(pipelineRows(REAL, null));
  const flat = phases.flatMap((p) => p.items.flatMap((it) =>
    (it.type === "bracket" ? [it.parent, ...it.rows] : [it.row])));

  assert.deepEqual(flat.map((r) => r.node), REAL.map((s) => s.node));
});

test("a step added to the graph is placed by where it sits, not dropped", () => {
  const steps = [step("guard", "code"), ...REAL.slice(0, 11), step("polish", "code"), ...REAL.slice(11)];
  const phases = pipelinePhases(pipelineRows(steps, null));

  assert.equal(nodes(phases[0].items)[0], "guard");
  assert.equal(nodes(phases[2].items)[0], "polish");
});

test("without the router the answering phase opens on the bracket", () => {
  const steps = REAL.filter((s) => s.node !== "layer_router");
  const phases = pipelinePhases(pipelineRows(steps, null));

  assert.deepEqual(nodes(phases[0].items), ["datasource_resolver", "decomposer"]);
  assert.equal(phases[1].items[0].type, "bracket");
});

test("without a nested step there is no bracket: the answering phase is left out", () => {
  const flat = [step("a", "model"), step("b", "code")];
  const phases = pipelinePhases(pipelineRows(flat, null));

  assert.deepEqual(phases.map((p) => p.id), ["understand"]);
  assert.deepEqual(nodes(phases[0].items), ["a", "b"]);
  assert.deepEqual(pipelinePhases([]), []);
  assert.deepEqual(pipelinePhases(null), []);
});

test("PHASES names the three phases in reading order", () => {
  assert.deepEqual(PHASES.map((p) => p.id), ["understand", "answer", "combine"]);
});

const RESULT = {
  timings: { LangGraph: 5.04, datasource_resolver: 0.86, decomposer: 1.18, layer_router: 0.001,
    sql_agent: 2.61, schema_retriever: 0.21, ast_planner: 2.31, logical_validator: 0.004,
    generator: 0.006, executor: 0.031, aggregator: 0.001, answer_synthesizer: 0.12 },
};

test("stepStates: nothing before a run, then done, skipped or did not run", () => {
  assert.deepEqual(stepStates(pipelineRows(REAL, null), false),
    Object.fromEntries(REAL.map((s) => [s.node, "idle"])));

  const states = stepStates(pipelineRows(REAL, RESULT), true);
  assert.equal(states.ast_planner, "done");
  // A later step ran, so these were passed over rather than never reached.
  assert.equal(states.retry_handler, "skipped");
  assert.equal(states.refiner, "skipped");
  assert.equal(states.answer_synthesizer, "done");
});

test("stepStates: a run that stopped leaves the rest as did not run", () => {
  const stopped = { timings: { datasource_resolver: 0.5, decomposer: 0.4, ast_planner: 1.2 } };
  const states = stepStates(pipelineRows(REAL, stopped), true);

  assert.equal(states.layer_router, "skipped");
  assert.equal(states.ast_planner, "done");
  assert.equal(states.logical_validator, "not-run");
  assert.equal(states.answer_synthesizer, "not-run");
});

test("waterfall lays the steps end to end, the bracket's steps inside the SQL agent's span", () => {
  const bars = waterfall(pipelineRows(REAL, RESULT), RESULT);
  const near = (a, b) => Math.abs(a - b) < 0.05;

  assert.equal(bars.datasource_resolver.start, 0);
  assert.ok(near(bars.datasource_resolver.width, 0.86 / 5.04 * 100));
  assert.ok(near(bars.decomposer.start, 0.86 / 5.04 * 100));
  // The SQL agent starts after the router; its first step starts with it.
  const agentStart = (0.86 + 1.18 + 0.001) / 5.04 * 100;
  assert.ok(near(bars.sql_agent.start, agentStart));
  assert.ok(near(bars.schema_retriever.start, agentStart));
  assert.ok(near(bars.ast_planner.start, (0.86 + 1.18 + 0.001 + 0.21) / 5.04 * 100));
  // After the bracket, time resumes from the end of the SQL agent's span.
  assert.ok(near(bars.aggregator.start, (0.86 + 1.18 + 0.001 + 2.61) / 5.04 * 100));
  // A step that did not run has no bar.
  assert.equal(bars.refiner, undefined);
});

test("waterfall never draws past the end, and has nothing to draw before a run", () => {
  const long = { timings: { LangGraph: 1, datasource_resolver: 0.8, decomposer: 0.8 } };
  const bars = waterfall(pipelineRows(REAL, long), long);

  for (const bar of Object.values(bars)) {
    assert.ok(bar.start >= 0 && bar.start + bar.width <= 100.0001);
  }
  assert.deepEqual(waterfall(pipelineRows(REAL, null), null), {});
});
