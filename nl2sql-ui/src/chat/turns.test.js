// Run with `npm test`. What the transcript reads off a stored Turn (api/fixtures/Turn.json is the real shape).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { answerOf, askSteps, errorLine, offersFor, outputsOf, pendingApprovals, pendingClarification, statusLine, toolArgs, toolButtons, toolLabel } from "./turns.js";

const turn = (patch = {}) => ({ ...JSON.parse(readFileSync(new URL("../api/fixtures/Turn.json", import.meta.url), "utf8")), ...patch });
const step = (name, patch = {}) => ({ call: { name, args: {} }, status: "ok", question: null, trace_id: null, result: null, output: null, error: null, ...patch });

test("a turn's answer is its reply", () => {
  assert.equal(answerOf(turn()), "There are 59 customers.");
  assert.equal(answerOf(turn({ reply: "" })), "");
});

test("tool names read as words", () => {
  assert.equal(toolLabel("dashboard_pin"), "Pin to dashboard");
  assert.equal(toolLabel("chart"), "Chart");
  assert.equal(toolLabel("some_tool"), "Some tool");
});

test("ask steps are the ones that ran a question", () => {
  const t = turn({ steps: [step("ask", { question: "a" }), step("chart")] });
  assert.deepEqual(askSteps(t).map((s) => s.question), ["a"]);
});

test("the clarification and the approvals a turn waits on come from its results", () => {
  const clarification = { question: "Which?", options: [{ id: "a", label: "A" }] };
  const t = turn({ status: "needs_clarification", steps: [step("ask", { status: "needs_clarification", result: { clarification } })] });
  assert.equal(pendingClarification(t), clarification);
  assert.equal(pendingClarification(turn()), null);
  const need = [{ id: "ap-1", question: "q" }];
  const w = turn({ status: "needs_approval", steps: [step("ask", { result: { approvals_needed: need } })] });
  assert.deepEqual(pendingApprovals(w), need);
  assert.deepEqual(pendingApprovals(turn()), []);
});

test("tool outputs are the steps that made one", () => {
  const output = { kind: "chart", title: "T", body: {}, refs: [] };
  assert.deepEqual(outputsOf(turn({ steps: [step("chart", { output }), step("ask")] })), [{ index: 0, output }]);
});

test("offers are the tool calls the model proposed", () => {
  const call = { name: "chart", args: { kind: "bar" } };
  assert.deepEqual(offersFor(turn({ offers: [call] })), [call]);
});

test("tool buttons: only the tools the server lists, pin only inside a dashboard conversation", () => {
  const tools = [{ name: "chart" }, { name: "diagram" }, { name: "report" }, { name: "dashboard_pin" }];
  const t = turn({ steps: [step("ask", { result: { trace_id: "r1" } })] });
  assert.deepEqual(toolButtons(t, { dashboard_id: null }, tools).map((b) => b.name), ["chart", "diagram", "report"]);
  assert.deepEqual(toolButtons(t, { dashboard_id: "d1" }, tools).map((b) => b.name), ["chart", "diagram", "report", "dashboard_pin"]);
  assert.deepEqual(toolButtons(t, { dashboard_id: null }, [{ name: "chart" }]).map((b) => b.name), ["chart"]);
  assert.deepEqual(toolButtons(turn({ status: "error" }), {}, tools), []);
});

test("status and error lines say what stopped the turn", () => {
  assert.equal(statusLine(turn()), null);
  assert.match(statusLine(turn({ status: "needs_clarification" })), /meaning/i);
  assert.match(statusLine(turn({ status: "needs_approval" })), /approval/i);
  assert.match(statusLine(turn({ status: "stopped" })), /stopped/i);
  assert.equal(errorLine(turn({ status: "error", error: { message: "boom" } })), "boom");
  assert.equal(errorLine(turn()), null);
});

test("a tool button runs on this turn's first answer; a report covers the conversation", () => {
  const t = turn({ index: 3 });
  assert.deepEqual(toolArgs("chart", t), { turn: 3, step: 1 });
  assert.deepEqual(toolArgs("diagram", t), { view: "answer", turn: 3, step: 1 });
  assert.deepEqual(toolArgs("report", t), {});
  assert.deepEqual(toolArgs("dashboard_pin", t), {});
});
