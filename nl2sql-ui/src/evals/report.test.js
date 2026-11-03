// The report as the page reads it. The board below has the shape `score_config` in nl2sql/evaluation/report.py
// builds (trimmed to what the page reads), because the committed `Job` fixture's report ran no cases.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { failureMessages, money, percent, reportView, stoppedText } from "./report.js";

const result = (id, role, status, scores, extra = {}) => ({
  suite: "chinook", id, question: `Question ${id}`, role, status, source: "generated", sql: "SELECT 1", scores, ...extra,
});

const BOARD = {
  summary: {
    by_role: { admin: { pass: 1, fail: 1, error: 0, skip: 0 }, viewer: { pass: 1, fail: 0, error: 1, skip: 0 } },
    total: { pass: 2, fail: 1, error: 1, skip: 0 },
  },
  accuracy: {
    overall: 0.5, lenient: 0.5, generated: 0.3333, execution: 0.5, interval: [0.15, 0.85],
    by_source: {
      generated: { pass: 1, total: 3, accuracy: 0.3333 },
      verified: { pass: 1, total: 1, accuracy: 1.0 },
    },
  },
  cost: { total: 0.05, per_question: 0.0125 },
  latency: { question: { p50: 2.0, p95: 4.5 } },
  retries: { total: 1, questions_with_retries: 1 },
  results: [
    result("q1", "admin", "pass", { result_match: { passed: true, detail: {} } }),
    result("q2", "admin", "fail", {
      result_match: { passed: false, detail: { reason: "rows differ" } },
      faithfulness: { passed: false, detail: { reason: "13 is not in the rows" } },
      outcome: { passed: true, detail: {} },
    }),
    result("q1", "viewer", "pass", {}, { source: "verified" }),
    result("q3", "viewer", "error", { result_match: { passed: null, detail: { error: "boom" } } }),
  ],
};

const REPORT = {
  mode: "full", suites: [{ name: "chinook", path: "evals/chinook.yaml", digest: "d1" }], spent: 0.05, max_cost_usd: 1,
  stopped: null, skipped: 0, unknown_cost_calls: 0, configs: { default: BOARD },
};

test("the committed Job fixture's report reads, with no boards", () => {
  const job = JSON.parse(readFileSync(new URL("../api/fixtures/JobEval.json", import.meta.url), "utf8"));
  const view = reportView(job.result);
  assert.equal(view.mode, "plans");
  assert.deepEqual(view.boards, []);
  assert.deepEqual(view.suites, ["chinook"]);
});

test("a board gives accuracy, the generated path, each source and each role", () => {
  const [board] = reportView(REPORT).boards;
  assert.equal(board.name, "default");
  assert.equal(board.accuracy, 0.5);
  assert.deepEqual(board.interval, [0.15, 0.85]);
  assert.equal(board.generated, 0.3333);
  assert.deepEqual(board.bySource, [
    { name: "generated", pass: 1, total: 3, accuracy: 0.3333 },
    { name: "verified", pass: 1, total: 1, accuracy: 1 },
  ]);
  assert.deepEqual(board.byRole, [
    { role: "admin", pass: 1, fail: 1, error: 0, skip: 0, total: 2 },
    { role: "viewer", pass: 1, fail: 0, error: 1, skip: 0, total: 2 },
  ]);
  assert.deepEqual([board.cost, board.perQuestion, board.p50, board.p95, board.retries], [0.05, 0.0125, 2, 4.5, 1]);
});

test("each failing case carries the messages of the scorers that failed", () => {
  const [board] = reportView(REPORT).boards;
  assert.deepEqual(board.failing.map((f) => [f.id, f.role, f.status]), [["q2", "admin", "fail"], ["q3", "viewer", "error"]]);
  assert.deepEqual(board.failing[0].messages, ["result_match: rows differ", "faithfulness: 13 is not in the rows"]);
  assert.deepEqual(board.failing[1].messages, ["result_match: boom"]);
  assert.equal(board.failing[0].question, "Question q2");
});

test("a failing case with no scorer detail falls back to the record's reason", () => {
  assert.deepEqual(failureMessages({ status: "fail", reason: "outcome: wrong", scores: {} }), ["outcome: wrong"]);
  assert.deepEqual(failureMessages({ status: "fail", scores: {} }), ["The case failed."]);
});

test("a run that stopped says why", () => {
  assert.equal(stoppedText("max_cost", 1), "Stopped at the cost cap of $1.00.");
  assert.equal(stoppedText("cancelled"), "Cancelled before every case ran.");
  assert.equal(stoppedText(null), null);
});

test("percent and money are plain figures, and say so when there is none", () => {
  assert.equal(percent(0.3333), "33%");
  assert.equal(percent(1), "100%");
  assert.equal(percent(null), "n/a");
  assert.equal(money(0.0125), "$0.0125");
  assert.equal(money(2.5), "$2.50");
  assert.equal(money(null), "n/a");
});

test("several configs are several boards, each named", () => {
  const view = reportView({ ...REPORT, configs: { a: BOARD, b: { ...BOARD, accuracy: { ...BOARD.accuracy, overall: 1 } } } });
  assert.deepEqual(view.boards.map((b) => [b.name, b.accuracy]), [["a", 0.5], ["b", 1]]);
});

test("spent, the cap and the cases with no known cost are carried", () => {
  const view = reportView({ ...REPORT, stopped: "max_cost", unknown_cost_calls: 2, skipped: 3 });
  assert.deepEqual([view.spent, view.cap, view.unknownCost, view.skipped], [0.05, 1, 2, 3]);
  assert.equal(view.stopped, "Stopped at the cost cap of $1.00.");
});
