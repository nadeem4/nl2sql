// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  STOP_AFTER_MS, answerHead, askButton, chipRow, elapsedLabel, gateReason, gateTimeline, runFault,
  secs, stationHeads, stationStates, statusStrip,
} from "./runState.js";

const passed = (name) => ({ name, passed: true, message: `${name} ok` });
const sub = (over = {}) => ({
  plan: { tables: [{ name: "Genre" }] },
  validation: [passed("plan_present"), passed("structure_and_schema"), passed("policy")],
  sql: "SELECT 1",
  rows: { columns: ["n"], rows: [[1]], total_rows: 10 },
  retry_count: 0,
  ...over,
});
const result = (over = {}) => ({
  status: "success",
  sub_queries: [sub()],
  errors: [],
  warnings: [],
  final_answer: { summary: "Rock sells the most tracks." },
  usage: { total: { calls: 3, cost: 0.0187 }, nodes: { ast_planner: { calls: 2, model: "gpt-5.4" } } },
  timings: { LangGraph: 5.04, ast_planner: 2.31, logical_validator: 0.004, generator: 0.006, executor: 0.031 },
  ...over,
});

// ---------- station states ----------

test("nothing asked: every station is idle", () => {
  assert.deepEqual(stationStates({ asked: null }), {
    plan: "idle", checks: "idle", sql: "idle", rows: "idle", cost: "idle",
  });
});

test("busy with no per-node progress: the first station is live, the rest are queued", () => {
  assert.deepEqual(stationStates({ asked: {}, busy: true }), {
    plan: "busy", checks: "queued", sql: "queued", rows: "queued", cost: "queued",
  });
});

test("stopped: every station is skipped", () => {
  assert.deepEqual(stationStates({ asked: {}, stopped: true }), {
    plan: "skipped", checks: "skipped", sql: "skipped", rows: "skipped", cost: "skipped",
  });
});

test("an answered run is done throughout", () => {
  const r = result();
  assert.deepEqual(stationStates({ asked: {}, result: r, sub: r.sub_queries[0] }), {
    plan: "done", checks: "done", sql: "done", rows: "done", cost: "done",
  });
});

test("a refused plan stops at the checks and skips SQL and rows", () => {
  const s = sub({ validation: [passed("plan_present"), { name: "policy", passed: false, message: "no" }], sql: "", rows: null });
  const r = result({ sub_queries: [s] });
  assert.deepEqual(stationStates({ asked: {}, result: r, sub: s }), {
    plan: "done", checks: "stopped", sql: "skipped", rows: "skipped", cost: "done",
  });
});

test("no sub-query back (a provider refused): only the cost station ran", () => {
  const r = result({ sub_queries: [] });
  assert.deepEqual(stationStates({ asked: {}, result: r, sub: undefined }), {
    plan: "skipped", checks: "skipped", sql: "skipped", rows: "skipped", cost: "done",
  });
});

test("plan only holds the rows station", () => {
  const s = sub({ rows: null });
  const r = result({ status: "plan_only", sub_queries: [s] });
  assert.equal(stationStates({ asked: {}, result: r, sub: s }).rows, "held");
});

// ---------- station heads ----------

test("a model step names its model from usage; code steps say Code", () => {
  const r = result();
  const heads = stationHeads(r, { plan: "done", checks: "done", sql: "done", rows: "done", cost: "done" });
  assert.deepEqual(heads.plan, { kind: "model", tag: "Model · gpt-5.4", time: "2.31 s" });
  assert.deepEqual(heads.checks, { kind: "code", tag: "Code", time: "4 ms" });
  assert.deepEqual(heads.sql, { kind: "code", tag: "Code", time: "6 ms" });
  assert.deepEqual(heads.rows, { kind: "code", tag: "Code", time: "31 ms" });
  assert.deepEqual(heads.cost, { kind: "code", tag: null, time: "5.04 s" });
});

test("a model step with no usage reported still says Model", () => {
  const heads = stationHeads(null, { plan: "idle", checks: "idle", sql: "idle", rows: "idle", cost: "idle" });
  assert.equal(heads.plan.tag, "Model");
  assert.equal(heads.plan.time, null);
});

test("queued stations say waiting; skipped ones say not reached", () => {
  const heads = stationHeads(null, { plan: "busy", checks: "queued", sql: "skipped", rows: "queued", cost: "queued" });
  assert.equal(heads.plan.time, null);
  assert.equal(heads.checks.time, "waiting");
  assert.equal(heads.sql.time, "not reached");
});

// ---------- the answer header ----------

test("an answer leads with the summary", () => {
  const r = result();
  assert.deepEqual(answerHead(r, r.sub_queries[0]), { kind: "answer", text: "Rock sells the most tracks." });
});

test("a refusal gets the same billing", () => {
  const s = sub({ validation: [{ name: "policy", passed: false, message: "no" }], sql: "", rows: null });
  assert.deepEqual(answerHead(result({ sub_queries: [s] }), s), {
    kind: "refused", text: "Refused at the checks. No SQL was written.",
  });
});

test("plan only, and rows with no summary, still say what came back", () => {
  const s = sub({ rows: null });
  assert.equal(answerHead(result({ status: "plan_only", sub_queries: [s], final_answer: null }), s).kind, "plan_only");
  const t = sub();
  assert.deepEqual(answerHead(result({ final_answer: null }), t), { kind: "answer", text: "10 rows came back." });
});

test("no result means no answer header text", () => {
  assert.equal(answerHead(null, null), null);
});

// ---------- the status strip ----------

test("the strip counts rows, checks, plans, time and cost, each pointing at its station", () => {
  const r = result({ sub_queries: [sub({ retry_count: 1 })] });
  assert.deepEqual(statusStrip(r, r.sub_queries[0]), [
    { label: "10 rows", target: "pane-rows", tone: null },
    { label: "3 of 3 checks", target: "pane-validation", tone: "ok" },
    { label: "2 plans", target: "pane-validation", tone: null },
    { label: "5.04 s", target: "pane-usage", tone: null },
    { label: "$0.0187", target: "pane-usage", tone: null },
  ]);
});

test("one row, a refused check, one plan and no price", () => {
  const s = sub({
    validation: [passed("plan_present"), { name: "policy", passed: false, message: "no" }],
    rows: { columns: ["n"], rows: [[1]], total_rows: 1 },
  });
  const r = result({ sub_queries: [s], usage: { total: { calls: 1, cost: null } } });
  assert.deepEqual(statusStrip(r, s), [
    { label: "1 row", target: "pane-rows", tone: null },
    { label: "1 of 2 checks", target: "pane-validation", tone: "fault" },
    { label: "5.04 s", target: "pane-usage", tone: null },
  ]);
});

test("no result, no strip", () => {
  assert.deepEqual(statusStrip(null, null), []);
});

// ---------- the gate ----------

test("no retries, no timeline", () => {
  assert.equal(gateTimeline(sub()), null);
});

test("one retry that then passed reads Plan 1 refused, Plan 2 passed", () => {
  assert.deepEqual(gateTimeline(sub({ retry_count: 1 })), [
    { label: "Plan 1 refused", passed: false },
    { label: "Plan 2 passed", passed: true },
  ]);
});

test("retries that never passed end refused", () => {
  const s = sub({ retry_count: 1, validation: [{ name: "policy", passed: false, message: "no" }] });
  assert.deepEqual(gateTimeline(s), [
    { label: "Plan 1 refused", passed: false },
    { label: "Plan 2 refused", passed: false },
  ]);
});

test("the reason is one sentence: the refusal, a role denial, or why a retry was needed", () => {
  const denied = sub({ validation: [{ name: "policy", passed: false, message: "Role 'viewer' denied access to 'chinook.Customer'." }] });
  assert.deepEqual(gateReason(denied, result({ errors: [{ message: "Role 'viewer' denied access to 'chinook.Customer'." }] })),
    { kind: "denied", tables: ["Customer"] });
  const failed = sub({ validation: [{ name: "structure_and_schema", passed: false, message: "Column x does not exist." }] });
  assert.deepEqual(gateReason(failed, result()), { kind: "refused", text: "Column x does not exist." });
  const retried = sub({ retry_count: 1 });
  const r = result({
    errors: [{ error_code: "INVALID_COLUMN", message: "Column t.Genre does not exist on Track." }],
    warnings: [{ node: "refiner", message: "Join Genre on GenreId." }],
  });
  assert.deepEqual(gateReason(retried, r), {
    kind: "retried",
    text: "Plan 1 was refused: Column t.Genre does not exist on Track. The hint sent back: Join Genre on GenreId. No SQL was written until a plan passed.",
  });
  assert.equal(gateReason(sub(), result()), null);
});

// ---------- the fault ----------

test("the fault is the first error when nothing came back, or any provider error", () => {
  const entry = { error_code: "PROVIDER_AUTH_FAILED", message: "401", provider: "OpenAI" };
  assert.equal(runFault(result({ sub_queries: [], errors: [entry] }), undefined), entry);
  const r = result({ errors: [entry] });
  assert.equal(runFault(r, r.sub_queries[0]), entry);
  const other = result({ errors: [{ error_code: "INVALID_COLUMN", message: "x" }] });
  assert.equal(runFault(other, other.sub_queries[0]), null);
  assert.equal(runFault(result({ replay_miss: true, sub_queries: [], errors: [entry] }), undefined), null);
  assert.equal(runFault(null, null), null);
});

// ---------- busy: the button and the counter ----------

test("the button asks, then says Asking, then becomes Stop after 600ms", () => {
  assert.equal(STOP_AFTER_MS, 600);
  assert.deepEqual(askButton({ busy: false, elapsedMs: 0 }), { label: "Ask", action: "ask" });
  assert.deepEqual(askButton({ busy: true, elapsedMs: 200 }), { label: "Asking", action: "wait" });
  assert.deepEqual(askButton({ busy: true, elapsedMs: 600 }), { label: "Stop", action: "stop" });
});

test("the elapsed counter reads in tenths, the spoken one in whole seconds", () => {
  assert.equal(elapsedLabel(3400), "Running · 3.4 s");
  assert.equal(elapsedLabel(0), "Running · 0.0 s");
  assert.equal(elapsedLabel(3999, { spoken: true }), "Running, 3 seconds");
  assert.equal(elapsedLabel(1000, { spoken: true }), "Running, 1 second");
});

// ---------- the chip row ----------

const groups = [
  { datasource: "chinook", questions: ["a", "b", "c", "d", "e", "f"] },
  { datasource: "support", questions: ["g", "h"] },
];

test("chips: four from the selected datasource, the rest counted", () => {
  assert.deepEqual(chipRow(groups, "chinook", { ran: false }), {
    datasource: "chinook", chips: ["a", "b", "c", "d"], more: 4,
  });
});

test("chips: three once a run exists", () => {
  assert.deepEqual(chipRow(groups, "support", { ran: true }), {
    datasource: "support", chips: ["g", "h"], more: 6,
  });
  assert.equal(chipRow(groups, "chinook", { ran: true }).chips.length, 3);
});

test("chips: an unknown or empty selection falls back to the first pile", () => {
  assert.equal(chipRow(groups, null, {}).datasource, "chinook");
  assert.equal(chipRow(groups, "nope", {}).datasource, "chinook");
  assert.deepEqual(chipRow([], null, {}), { datasource: null, chips: [], more: 0 });
});

test("secs formats like the ledger", () => {
  assert.equal(secs(5.04), "5.04 s");
  assert.equal(secs(0.004), "4 ms");
  assert.equal(secs(0.0001), "<1 ms");
  assert.equal(secs(undefined), "-");
});
